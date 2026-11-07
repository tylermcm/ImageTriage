//! Isolated experiment. No egui, project writes, sidecars or production launch route.
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
    px: Vec<[f32; 4]>,
    curve: Vec<[f32; 2]>,
    size: [usize; 2],
    full: [u32; 2],
    display: Arc<Transform>,
    opens: u64,
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
    let reduced = photocraft_io::raw::import_raw_browse_reduced_to("spike.NEF", &bytes, edge)
        .map_err(|e| e.to_string())?
        .ok_or("Reduced sensor development unavailable; no embedded JPEG fallback")?;
    let doc = reduced.import.document;
    let area = doc.bounds();
    let mut px = vec![[0.0; 4]; area.width() as usize * area.height() as usize];
    let surf = doc
        .walk()
        .into_iter()
        .find_map(|(_, _, l)| match &l.content {
            photocraft_doc::LayerContent::Raster(s) => Some(s),
            _ => None,
        })
        .ok_or("No developed sensor pixels")?;
    surf.read_rgba_into(area, &mut px);
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
        px,
        curve: reduced.tone_curve,
        size: [area.width() as usize, area.height() as usize],
        full: [reduced.full_size.0, reduced.full_size.1],
        display,
        opens,
    })
}
fn frame(source: &Source, request: &Value, gpu: &mut Option<Gpu>) -> Result<(Value, Vec<u8>)> {
    let start = Instant::now();
    let exposure = request["exposure"].as_f64().unwrap_or(0.0);
    let shadows = request["shadows"].as_f64().unwrap_or(0.0);
    if !exposure.is_finite()
        || !shadows.is_finite()
        || !(-5.0..=5.0).contains(&exposure)
        || !(-100.0..=100.0).contains(&shadows)
    {
        return Err("Adjustment out of range".into());
    }
    let [w, h] = source.size;
    let mut px = source.px.clone();
    let p = CameraRaw {
        exposure: exposure as f32,
        encoding_gamma: 1.8,
        pixel_scale: w as f32 / source.full[0] as f32,
        ..Default::default()
    };
    // Same raw-stage Shadows processing as PhotoCraft's proxy job, before remaining RGB adjustments.
    if shadows != 0.0 {
        photocraft_io::raw::retone(
            &mut px,
            w,
            h,
            &source.curve,
            photocraft_io::raw::Tone {
                shadows: shadows as f32,
                ..Default::default()
            },
        );
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
            (shadows == 0.0).then_some(source.opens),
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
    let header = json!({"ok":true,"revision":request["revision"],"width":w,"height":h,"bytes":rgba.len(),
        "fullSize":source.full,"opens":source.opens,"backend":backend,"rawToneMs":tone_ms,"developMs":process_ms,
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
            if request["op"] == "open" {
                let start = Instant::now();
                source = None;
                opens += 1;
                source = Some(open(&request, opens)?);
                let mut result = frame(source.as_ref().ok_or("No source")?, &request, &mut gpu)?;
                result.0["openMs"] = json!(start.elapsed().as_secs_f64() * 1000.0);
                Ok(result)
            } else if request["op"] == "render" {
                frame(
                    source.as_ref().ok_or("Open a RAW first")?,
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
