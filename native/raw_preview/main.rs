//! Persistent RAW proxy worker. PhotoCraft processing, private stdio, no project writes.
#![forbid(unsafe_code)]
use photocraft_algo::{
    camera_raw::{CameraRaw, develop, per_pixel},
    histogram::RgbHistogram,
};
use photocraft_cms::{Builtin, Transform, TransformOptions};
use rayon::prelude::*;
use serde_json::{Value, json};
use std::{
    io::{self, BufRead, Write},
    sync::Arc,
    time::Instant,
};

type Result<T> = std::result::Result<T, String>;
struct Source {
    sensor: photocraft_raw::Sensor,
    options: photocraft_raw::DevelopOptions,
    base: Raster,
    quick: Raster,
    tuned: Option<(Vec<u32>, Raster, Raster)>,
    edge: usize,
    full: [u32; 2],
    display: Arc<Transform>,
    opens: u64,
}
struct Raster {
    px: Vec<[f32; 4]>,
    size: [usize; 2],
}

fn drag_frame(source: &Raster) -> Raster {
    // Reduce developed pixels, not the sensor a second time. Different Bayer
    // reduction factors trim different edge remainders and shift the framing.
    let [w, h] = source.size;
    let scale = (1200.0 / w.max(h) as f64).min(1.0);
    let size = [
        (w as f64 * scale).round() as usize,
        (h as f64 * scale).round() as usize,
    ];
    let px =
        photocraft_algo::seam::resize_bilinear(w, h, 4, source.px.as_flattened(), size[0], size[1])
            .as_chunks::<4>()
            .0
            .to_vec();
    Raster { px, size }
}

fn sensor_frame(
    sensor: &photocraft_raw::Sensor,
    options: &photocraft_raw::DevelopOptions,
    edge: usize,
) -> Result<(Raster, [u32; 2])> {
    let reduced =
        photocraft_raw::develop_reduced(sensor, options, edge).map_err(|e| e.to_string())?;
    let developed = reduced.developed;
    let px = developed
        .rgb
        .as_chunks::<3>()
        .0
        .iter()
        .map(|p| {
            [
                p[0] as f32 / 65535.0,
                p[1] as f32 / 65535.0,
                p[2] as f32 / 65535.0,
                1.0,
            ]
        })
        .collect();
    Ok((
        Raster {
            px,
            size: [developed.width as usize, developed.height as usize],
        },
        [reduced.full_width, reduced.full_height],
    ))
}
struct Gpu {
    device: wgpu::Device,
    queue: wgpu::Queue,
    pass: photocraft_gpu::CameraRawDevelop,
}
impl Gpu {
    fn new() -> Result<Self> {
        let mut descriptor = wgpu::InstanceDescriptor::new_without_display_handle();
        descriptor.backends = wgpu::Backends::DX12;
        // Match PhotoCraft's safe fallback when no DXC is shipped beside the
        // executable; PATH can contain an incompatible dxcompiler.dll.
        descriptor.backend_options.dx12.shader_compiler = wgpu::Dx12Compiler::Fxc;
        let instance = wgpu::Instance::new(descriptor);
        let adapter = pollster::block_on(instance.request_adapter(&Default::default()))
            .map_err(|e| e.to_string())?;
        if adapter.get_info().device_type == wgpu::DeviceType::Cpu
            || !photocraft_gpu::CameraRawDevelop::supported(&adapter)
        {
            return Err("No supported hardware GPU".into());
        }
        eprintln!("GPU: {}", adapter.get_info().name);
        let (device, queue) = pollster::block_on(adapter.request_device(&Default::default()))
            .map_err(|e| e.to_string())?;
        device.set_device_lost_callback(|reason, message| {
            eprintln!("GPU device lost: {reason:?}: {message}")
        });
        let pass = photocraft_gpu::CameraRawDevelop::new(&device).map_err(|e| e.to_string())?;
        Ok(Self {
            device,
            queue,
            pass,
        })
    }
}
fn open(p: &Value, opens: u64) -> Result<Source> {
    let path = p["path"].as_str().ok_or("Missing source path")?;
    let edge = p["edge"].as_u64().unwrap_or(1200).clamp(256, 2400) as usize;
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let limits = photocraft_raw::Limits {
        max_pixels: 128_000_000,
        max_alloc: 512 * 1024 * 1024,
        ..Default::default()
    };
    let sensor = photocraft_raw::decode(&bytes, &limits).map_err(|e| e.to_string())?;
    // Same default-look selection as PhotoCraft's RAW import; fit once per source.
    let camera_look = if sensor.color.calibrations.is_empty() && !sensor.is_monochrome() {
        photocraft_io::raw::fit_preview_look(
            &bytes,
            &sensor,
            &photocraft_raw::DevelopOptions {
                limits,
                ..Default::default()
            },
        )
        .ok()
    } else {
        None
    };
    let options = photocraft_raw::DevelopOptions {
        limits,
        camera_look,
        ..Default::default()
    };
    let (base, full) = sensor_frame(&sensor, &options, edge)?;
    let quick = drag_frame(&base);
    // RAW import is ProPhoto-compatible, gamma 1.8, just like PhotoCraft's open-time proxy.
    let display = photocraft_cms::cached(
        Builtin::ProPhotoCompat.profile(),
        Builtin::Srgb.profile(),
        TransformOptions {
            precise_float: false,
            ..Default::default()
        },
    )
    .map_err(|e| e.to_string())?;
    Ok(Source {
        sensor,
        options,
        base,
        quick,
        tuned: None,
        edge,
        full,
        display,
        opens,
    })
}

fn frame(source: &mut Source, request: &Value, gpu: &mut Option<Gpu>) -> Result<(Value, Vec<u8>)> {
    let start = Instant::now();
    let recipe = request.get("recipe").ok_or("Missing complete recipe")?;
    if recipe["version"] != 1 || !recipe["cameraRaw"].is_object() {
        return Err("Unsupported RAW recipe".into());
    }
    let mut p: CameraRaw =
        serde_json::from_value(recipe["cameraRaw"].clone()).map_err(|e| e.to_string())?;
    p.validate_changes(&CameraRaw::default())?;
    p.validate_retouch_size(source.full[0] as usize,source.full[1] as usize)?;
    let quick = request["quality"] == "draft";
    let raw_values = [
        p.temperature,
        p.tint,
        p.exposure,
        p.shadows,
        p.highlights,
        p.clarity,
        p.texture,
        p.dehaze,
    ];
    let changed = raw_values.iter().any(|v| *v != 0.0);
    let key: Vec<u32> = raw_values.iter().map(|v| v.to_bits()).collect();
    if changed
        && source
            .tuned
            .as_ref()
            .is_none_or(|(cached, _, _)| cached != &key)
    {
        let mut options = source.options.clone();
        options.exposure = p.exposure as f64;
        options.tone = photocraft_io::raw::Tone {
            shadows: p.shadows,
            highlights: p.highlights,
            clarity: p.clarity,
            texture: p.texture,
            dehaze: p.dehaze,
        };
        if let Some(wb) = source.sensor.camera_wb {
            let gains = photocraft_algo::camera_raw::white_balance_gains(p.temperature, p.tint);
            options.white_balance = photocraft_raw::WhiteBalance::Multipliers([
                wb[0] * gains[0] as f64,
                wb[1] * gains[1] as f64,
                wb[2] * gains[2] as f64,
            ]);
        }
        let (raster, _) = sensor_frame(&source.sensor, &options, source.edge)?;
        let drag = drag_frame(&raster);
        source.tuned = Some((key, raster, drag));
    }
    let raster = if changed {
        let tuned = source.tuned.as_ref().ok_or("Missing sensor preview")?;
        if quick { &tuned.2 } else { &tuned.1 }
    } else if quick {
        &source.quick
    } else {
        &source.base
    };
    let [w, h] = raster.size;
    let mut px = raster.px.clone();
    p.encoding_gamma = 1.8;
    p.pixel_scale = w as f32 / source.full[0] as f32;
    (
        p.exposure,
        p.shadows,
        p.highlights,
        p.clarity,
        p.texture,
        p.dehaze,
    ) = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
    if source.sensor.camera_wb.is_some() {
        p.temperature = 0.0;
        p.tint = 0.0;
    }
    let tone_ms = start.elapsed().as_secs_f64() * 1000.0;
    let process = Instant::now();
    let mut backend = "cpu";
    if request["backend"].as_str() != Some("cpu")
        && !p.is_identity()
        && let Some(g) = gpu.as_mut()
        && let Some(stages) = per_pixel(&p, w, h, false, None)
    {
        match g.pass.develop(
            &g.device,
            &g.queue,
            None,
            &px,
            (!changed).then_some(source.opens.saturating_mul(2) + u64::from(quick)),
            [w as u32, h as u32],
            &stages,
        ) {
            Ok(result) => {
                px = result;
                backend = "gpu";
            }
            Err(e) => {
                eprintln!("GPU fallback: {e}");
                *gpu = None;
            }
        }
    }
    if backend == "cpu" {
        develop(&mut px, w, h, &p, false);
    }
    let process_ms = process.elapsed().as_secs_f64() * 1000.0;
    if request.get("coverage").is_some() {
        let mask: photocraft_algo::camera_raw::LocalMask = serde_json::from_value(request["coverage"].clone()).map_err(|e|e.to_string())?;
        let check = CameraRaw { masks:vec![mask.clone()],..CameraRaw::default() };
        check.validate()?;
        let coverage = mask.coverage_fn(w,h);
        let bytes: Vec<u8> = px.par_iter().enumerate().flat_map_iter(|(i,p)| {
            let alpha = (coverage.at_px(i%w,i/w,[p[0],p[1],p[2]]).clamp(0.0,1.0)*90.0).round() as u8;
            [255,70,95,alpha]
        }).collect();
        return Ok((json!({"ok":true,"protocol":2,"maskCoverage":true,"revision":request["revision"],"width":w,"height":h,"bytes":bytes.len(),"fullSize":source.full}),bytes));
    }
    if let Some(area) = request["sample"].as_array() {
        if area.len() != 4 { return Err("Sample needs four normalized coordinates".into()); }
        let values: Vec<f64> = area.iter().map(|v| v.as_f64().ok_or("Invalid sample coordinate".to_string())).collect::<Result<_>>()?;
        let x0 = (values[0].min(values[2]).clamp(0.0,1.0)*(w-1) as f64).floor() as usize;
        let x1 = (values[0].max(values[2]).clamp(0.0,1.0)*(w-1) as f64).ceil() as usize;
        let y0 = (values[1].min(values[3]).clamp(0.0,1.0)*(h-1) as f64).floor() as usize;
        let y1 = (values[1].max(values[3]).clamp(0.0,1.0)*(h-1) as f64).ceil() as usize;
        let step = (((x1-x0+1)*(y1-y0+1)) as f64/4096.0).sqrt().ceil().max(1.0) as usize;
        let mut rgb = [0.0f32; 4];
        let (mut low, mut high, mut count) = (f32::INFINITY,f32::NEG_INFINITY,0.0f32);
        for y in (y0..=y1).step_by(step) {
            for x in (x0..=x1).step_by(step) {
                let p = px[y*w+x];
                for c in 0..3 { rgb[c] += p[c]; }
                let luma = 0.2126*p[0]+0.7152*p[1]+0.0722*p[2];
                low = low.min(luma); high = high.max(luma); count += 1.0;
            }
        }
        for c in 0..3 { rgb[c] = (rgb[c]/count).clamp(-0.5,2.0); }
        rgb[3] = 1.0;
        let mut display = [0.0f32; 4];
        source.display.convert_f32(&rgb,4,&mut display,4,true);
        return Ok((json!({"ok":true,"protocol":2,"bytes":0,"revision":request["revision"],
            "sampleRGB":&rgb[..3],"sampleDisplay":&display[..3],"sampleLuminance":[low.clamp(0.0,1.0),high.clamp(0.0,1.0)]}),vec![]));
    }
    let histogram_start = Instant::now();
    let histogram = RgbHistogram::from_rgba(&px);
    let histogram_ms = histogram_start.elapsed().as_secs_f64() * 1000.0;
    let display_start = Instant::now();
    let mut display = vec![0.0f32; px.len() * 4];
    display
        .par_chunks_mut(65536)
        .zip(px.par_chunks(16384))
        .for_each(|(dst, src)| {
            source
                .display
                .convert_f32(src.as_flattened(), 4, dst, 4, true);
        });
    let rgba: Vec<u8> = display
        .iter()
        .map(|v| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8)
        .collect();
    let header = json!({"ok":true,"protocol":2,"revision":request["revision"],"width":w,"height":h,"bytes":rgba.len(),
        "fullSize":source.full,"quality":if quick {"draft"} else {"settled"},"opens":source.opens,"backend":backend,"rawToneMs":tone_ms,"developMs":process_ms,
        "histogramMs":histogram_ms,"displayMs":display_start.elapsed().as_secs_f64()*1000.0,
        "workerMs":start.elapsed().as_secs_f64()*1000.0,
        "histogram":histogram.channels.iter().map(|c| c.to_vec()).collect::<Vec<_>>()});
    Ok((header, rgba))
}
fn run() -> Result<()> {
    let mut gpu = match Gpu::new() {
        Ok(g) => Some(g),
        Err(e) => {
            eprintln!("CPU fallback: {e}");
            None
        }
    };
    let mut source = None;
    let mut opens = 0;
    let mut output = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        let result = (|| -> Result<(Value, Vec<u8>)> {
            let request: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
            if request["op"] == "hello" {
                Ok((json!({"ok":true,"protocol":2,"bytes":0}), vec![]))
            } else if request["op"] == "open" {
                let start = Instant::now();
                source = None;
                opens += 1;
                source = Some(open(&request, opens)?);
                let mut result = frame(source.as_mut().ok_or("No source")?, &request, &mut gpu)?;
                result.0["openMs"] = json!(start.elapsed().as_secs_f64() * 1000.0);
                Ok(result)
            } else if request["op"] == "render" {
                frame(
                    source.as_mut().ok_or("Open a RAW first")?,
                    &request,
                    &mut gpu,
                )
            } else {
                Err("Unknown operation".into())
            }
        })();
        let (header, bytes) =
            result.unwrap_or_else(|e| (json!({"ok":false,"error":e,"bytes":0}), vec![]));
        writeln!(output, "{header}").map_err(|e| e.to_string())?;
        output
            .write_all(&bytes)
            .and_then(|_| output.flush())
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn drag_preview_preserves_settled_aspect_and_landmark_coordinates() {
        let size = [2064, 1376];
        let px = (0..size[0] * size[1])
            .map(|i| {
                [
                    (i % size[0]) as f32 / (size[0] - 1) as f32,
                    (i / size[0]) as f32 / (size[1] - 1) as f32,
                    0.0,
                    1.0,
                ]
            })
            .collect();
        let frame = drag_frame(&Raster { px, size });
        assert_eq!(frame.size, [1200, 800]);
        assert_eq!(frame.size[0] * size[1], frame.size[1] * size[0]);
        for [x, y] in [[0, 0], [300, 200], [600, 400], [900, 600], [1199, 799]] {
            let actual = frame.px[y * frame.size[0] + x];
            let expected = [x as f32 / 1199.0, y as f32 / 799.0];
            assert!((actual[0] - expected[0]).abs() < 0.001);
            assert!((actual[1] - expected[1]).abs() < 0.001);
        }
    }
}
