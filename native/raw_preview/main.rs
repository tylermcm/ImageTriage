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
    /// The raw's sensor; None for a picture that is not raw (JPEG, TIFF, PNG ...), which is edited as Camera Raw edits one: the
    /// whole recipe in the Camera Raw stage, on its pixels.
    sensor: Option<photocraft_raw::Sensor>,
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

/// A picture that is not raw (JPEG, TIFF, PNG, WebP ...), upright, in the editor's working space (ProPhoto, gamma 1.8, as a raw is
/// imported) from its own profile (sRGB when it has none), box-reduced so its longer side is at most `edge`; and its full size.
fn picture_frame(bytes: &[u8], edge: usize) -> Result<(Raster, [u32; 2])> {
    let image = photocraft_codecs::decode(bytes).map_err(|e| e.to_string())?;
    let (w, h) = (image.width() as usize, image.height() as usize);
    if w == 0 || h == 0 {
        return Err("The picture is empty".into());
    }
    let samples = image.to_rgba_f32();
    let own = image.icc.as_deref().and_then(|icc| photocraft_cms::Profile::parse(icc).ok());
    let to_working = photocraft_cms::cached(own.as_ref().unwrap_or(Builtin::Srgb.profile()), Builtin::ProPhotoCompat.profile(),
                                            TransformOptions { precise_float: false, ..Default::default() })
        .map_err(|e| e.to_string())?;
    let k = w.max(h).div_ceil(edge.max(1)).max(1);
    let (sw, sh) = (w.div_ceil(k), h.div_ceil(k));
    let mut reduced = vec![0.0f32; sw * sh * 4];
    reduced.par_chunks_mut(sw * 4).enumerate().for_each(|(sy, row)| {
        for sx in 0..sw {
            let (x0, y0) = (sx * k, sy * k);
            let (x1, y1) = ((x0 + k).min(w), (y0 + k).min(h));
            let mut sum = [0.0f32; 4];
            for y in y0..y1 {
                for x in x0..x1 {
                    let i = (y * w + x) * 4;
                    for c in 0..4 {
                        sum[c] += samples[i + c];
                    }
                }
            }
            let n = ((x1 - x0) * (y1 - y0)) as f32;
            for c in 0..4 {
                row[sx * 4 + c] = sum[c] / n;
            }
        }
    });
    let mut working = vec![0.0f32; reduced.len()];
    working.par_chunks_mut(65536).zip(reduced.par_chunks(65536)).for_each(|(dst, src)| to_working.convert_f32(src, 4, dst, 4, true));
    let px = working.chunks_exact(4).map(|q| [q[0], q[1], q[2], 1.0]).collect();
    Ok((Raster { px, size: [sw, sh] }, [w as u32, h as u32]))
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
        // The platform's own compute API, as the editor picks it (gpu_startup::backends): DX12 on Windows, Metal on macOS, Vulkan
        // elsewhere. This worker has no window, so GL (which needs a display to make a context) is never worth trying.
        descriptor.backends = if cfg!(windows) {
            wgpu::Backends::DX12
        } else if cfg!(target_os = "macos") {
            wgpu::Backends::METAL
        } else {
            wgpu::Backends::VULKAN
        };
        // Match PhotoCraft's safe fallback when no DXC is shipped beside the
        // executable; PATH can contain an incompatible dxcompiler.dll.
        #[cfg(windows)]
        {
            descriptor.backend_options.dx12.shader_compiler = wgpu::Dx12Compiler::Fxc;
        }
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
/// A source file's bytes. AI Denoise's companion file (`photocraft_raw::denoised`) is read as the original raw it names with the
/// denoised samples appended, as the decoder reads them.
fn read_source(path: &str) -> Result<Vec<u8>> {
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    // Super Resolution's base companion (`photocraft_raw::superres`): the original raw it names with the base appended.
    if let Some(head) = photocraft_raw::superres::base(&bytes)? {
        let original = photocraft_raw::denoised::resolve_original(path, &head.original);
        // The raw it was made from may be AI Denoise's companion: read as the denoised raw.
        let raw = read_source(&original).map_err(|e| format!("{original}: {e}"))?;
        return Ok(photocraft_raw::superres::expand(&raw, &bytes));
    }
    match photocraft_raw::denoised::companion(&bytes)? {
        Some(c) => {
            let original = photocraft_raw::denoised::resolve_original(path, &c.original);
            let raw = std::fs::read(&original).map_err(|e| format!("{original}: {e}"))?;
            Ok(photocraft_raw::denoised::expand(&raw, &c.trailer))
        }
        None => Ok(bytes),
    }
}
fn open(p: &Value, opens: u64) -> Result<Source> {
    let path = p["path"].as_str().ok_or("Missing source path")?;
    let edge = p["edge"].as_u64().unwrap_or(1200).clamp(256, 2400) as usize;
    let bytes = read_source(path)?;
    let display = photocraft_cms::cached(
        Builtin::ProPhotoCompat.profile(),
        Builtin::Srgb.profile(),
        TransformOptions {
            precise_float: false,
            ..Default::default()
        },
    )
    .map_err(|e| e.to_string())?;
    if !photocraft_io::raw::is_raw(&bytes) {
        let (base, full) = picture_frame(&bytes, edge)?;
        let quick = drag_frame(&base);
        return Ok(Source { sensor: None, options: Default::default(), base, quick, tuned: None, edge, full, display, opens });
    }
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
    Ok(Source {
        sensor: Some(sensor),
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
    // A picture that is not raw has no raw stage: these are developed with the rest, in the Camera Raw stage.
    let changed = source.sensor.is_some() && raw_values.iter().any(|v| *v != 0.0);
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
        let sensor = source.sensor.as_ref().ok_or("Missing sensor")?;
        if let Some(wb) = sensor.camera_wb {
            let gains = photocraft_algo::camera_raw::white_balance_gains(p.temperature, p.tint);
            options.white_balance = photocraft_raw::WhiteBalance::Multipliers([
                wb[0] * gains[0] as f64,
                wb[1] * gains[1] as f64,
                wb[2] * gains[2] as f64,
            ]);
        }
        let (raster, _) = sensor_frame(sensor, &options, source.edge)?;
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
    if let Some(sensor) = &source.sensor {
        // developed in the raw stage above
        (
            p.exposure,
            p.shadows,
            p.highlights,
            p.clarity,
            p.texture,
            p.dehaze,
        ) = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
        if sensor.camera_wb.is_some() {
            p.temperature = 0.0;
            p.tint = 0.0;
        }
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
/// AI Denoise: the crop's normalised CFA samples of the original raw at `path` (a file that
/// already carries denoised samples is read as its original), written as little-endian f32 to
/// `out`, with what the denoiser needs to know about them.
fn denoise_input(p: &Value) -> Result<(Value, Vec<u8>)> {
    let path = p["path"].as_str().ok_or("Missing source path")?;
    let out = p["out"].as_str().ok_or("Missing output path")?;
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let limits = photocraft_raw::Limits {
        max_pixels: 128_000_000,
        max_alloc: 768 * 1024 * 1024,
        ..Default::default()
    };
    let original = photocraft_raw::denoised::original(&bytes);
    let sensor = photocraft_raw::decode(original, &limits).map_err(|e| e.to_string())?;
    let input = photocraft_raw::denoised::denoise_input(&sensor)?;
    let mut data = Vec::with_capacity(input.samples.len() * 4);
    for v in &input.samples {
        data.extend_from_slice(&v.to_le_bytes());
    }
    std::fs::write(out, data).map_err(|e| e.to_string())?;
    let r = input.region;
    Ok((
        json!({"ok":true,"bytes":0,"region":[r.x,r.y,r.width,r.height],"sensor":[sensor.width,sensor.height],
               "pattern":input.pattern,"multipliers":input.daylight_multipliers,"originalBytes":original.len()}),
        vec![],
    ))
}
/// Super Resolution's base: the photo's demosaiced linear camera RGB over its crop (`photocraft_raw::superres`), from
/// `path` (a denoise companion is read with its denoising), written to `out` as little-endian half floats, interleaved RGB.
fn superres_input(p: &Value) -> Result<(Value, Vec<u8>)> {
    let path = p["path"].as_str().ok_or("Missing source path")?;
    let out = p["out"].as_str().ok_or("Missing output path")?;
    let bytes = read_source(path)?;
    let limits = photocraft_raw::Limits {
        max_pixels: 128_000_000,
        max_alloc: 1536 * 1024 * 1024,
        ..Default::default()
    };
    let sensor = photocraft_raw::decode(&bytes, &limits).map_err(|e| e.to_string())?;
    drop(bytes);
    let rgb = photocraft_raw::superres::linear_rgb(&sensor, photocraft_raw::Demosaic::default())?;
    let mut data = Vec::with_capacity(rgb.data.len() * 2);
    for v in &rgb.data {
        data.extend_from_slice(&photocraft_raw::denoised::f32_to_f16(*v).to_le_bytes());
    }
    std::fs::write(out, data).map_err(|e| e.to_string())?;
    let c = sensor.crop;
    Ok((
        json!({"ok":true,"bytes":0,"width":rgb.width,"height":rgb.height,"crop":[c.x,c.y,c.width,c.height],
               "sensor":[sensor.width,sensor.height],"neutral":rgb.neutral}),
        vec![],
    ))
}
/// A source developed region by region at full resolution (Super Resolution's base above all: a 2× LinearRaw frame of 180 MP,
/// far too large to develop whole for every edit). Kept decoded between requests, with what the last recipe measured over
/// the whole photo.
struct RegionSource {
    path: String,
    sensor: photocraft_raw::Sensor,
    /// The photo the statistics are measured on, reduced: the original raw of a base (the same sensor otherwise).
    measured: Option<photocraft_raw::Sensor>,
    options: photocraft_raw::DevelopOptions,
    /// The source's crop as decoded: the photo the fractions of a region are of.
    crop: photocraft_raw::Rect,
    /// The recipe the statistics are for, and what its raw stage and Camera Raw stage measured over the whole photo.
    stats: Option<(String, Arc<photocraft_raw::ToneStats>, Arc<photocraft_algo::camera_raw::WholeStats>)>,
}

/// The sensor rectangle (in the crop) of a rectangle given as fractions of the displayed (oriented) photo.
fn sensor_rect(o: u16, crop: photocraft_raw::Rect, r: [f64; 4]) -> photocraft_raw::Rect {
    let map = |u: f64, v: f64| -> (f64, f64) {
        match o {
            2 => (1.0 - u, v),
            3 => (1.0 - u, 1.0 - v),
            4 => (u, 1.0 - v),
            5 => (v, u),
            6 => (v, 1.0 - u),
            7 => (1.0 - v, 1.0 - u),
            8 => (1.0 - v, u),
            _ => (u, v),
        }
    };
    let corners = [map(r[0], r[1]), map(r[2], r[1]), map(r[0], r[3]), map(r[2], r[3])];
    let su0 = corners.iter().map(|c| c.0).fold(1.0f64, f64::min).clamp(0.0, 1.0);
    let su1 = corners.iter().map(|c| c.0).fold(0.0f64, f64::max).clamp(0.0, 1.0);
    let sv0 = corners.iter().map(|c| c.1).fold(1.0f64, f64::min).clamp(0.0, 1.0);
    let sv1 = corners.iter().map(|c| c.1).fold(0.0f64, f64::max).clamp(0.0, 1.0);
    let x0 = (su0 * crop.width as f64).floor() as usize;
    let y0 = (sv0 * crop.height as f64).floor() as usize;
    let x1 = ((su1 * crop.width as f64).ceil() as usize).clamp(x0 + 1, crop.width);
    let y1 = ((sv1 * crop.height as f64).ceil() as usize).clamp(y0 + 1, crop.height);
    photocraft_raw::Rect::new(crop.x + x0, crop.y + y0, x1 - x0, y1 - y0)
}

/// `(x, y, w, h)` of a rectangle of the crop (`x`, `y` from the crop's corner, a `cw × ch` crop) in the photo turned by
/// orientation `o`.
fn oriented_rect(o: u16, (cw, ch): (usize, usize), (x, y, w, h): (usize, usize, usize, usize)) -> (usize, usize, usize, usize) {
    match o {
        2 => (cw - x - w, y, w, h),
        3 => (cw - x - w, ch - y - h, w, h),
        4 => (x, ch - y - h, w, h),
        5 => (y, x, h, w),
        6 => (ch - y - h, x, h, w),
        7 => (ch - y - h, cw - x - w, h, w),
        8 => (y, cw - x - w, h, w),
        _ => (x, y, w, h),
    }
}

/// The long side the statistics of a region are measured at: the reduced copies they keep are at most 2464 px across.
const MEASURE_EDGE: usize = 2560;

/// `regionRender`: the part `rect` ([left, top, right, bottom], fractions of the displayed photo) of the source at `path`,
/// developed at full resolution with the whole recipe, as RGBA8 for display. A Super Resolution base companion is developed
/// from the base. What depends on the whole photo (Dehaze, Shadows, Highlights, Clarity, Texture, Whites, Blacks, the masks'
/// own) is measured once per recipe on a reduced development of the photo, so the region is its part of the whole picture.
fn region_render(cache: &mut Option<RegionSource>, request: &Value, display: &Arc<Transform>) -> Result<(Value, Vec<u8>)> {
    let start = Instant::now();
    let path = request["path"].as_str().ok_or("Missing source path")?;
    let rect: Vec<f64> = request["rect"].as_array().ok_or("Missing rect")?.iter().filter_map(Value::as_f64).collect();
    if rect.len() != 4 || !(rect[0] < rect[2] && rect[1] < rect[3]) {
        return Err("A region needs [left, top, right, bottom] fractions".into());
    }
    if cache.as_ref().is_none_or(|c| c.path != path) {
        *cache = None; // the old one goes first: a base is a gigabyte
        let bytes = read_source(path)?;
        let limits = photocraft_raw::Limits {
            max_pixels: 128_000_000,
            max_alloc: 512 * 1024 * 1024,
            ..Default::default()
        };
        let sensor = photocraft_raw::decode(&bytes, &limits).map_err(|e| e.to_string())?;
        let measured = match photocraft_raw::superres::split(&bytes) {
            Some((original, _)) => photocraft_raw::decode(original, &limits).ok(),
            None => None,
        };
        let camera_look = if sensor.color.calibrations.is_empty() && !sensor.is_monochrome() {
            photocraft_io::raw::fit_preview_look(&bytes, &sensor, &photocraft_raw::DevelopOptions { limits, ..Default::default() }).ok()
        } else {
            None
        };
        drop(bytes);
        let crop = sensor.crop;
        let options = photocraft_raw::DevelopOptions {
            limits: photocraft_raw::Limits { max_pixels: 400_000_000, max_alloc: 4096 * 1024 * 1024, ..Default::default() },
            camera_look,
            ..Default::default()
        };
        *cache = Some(RegionSource { path: path.to_string(), sensor, measured, options, crop, stats: None });
    }
    let src = cache.as_mut().ok_or("No source")?;
    let open_ms = start.elapsed().as_secs_f64() * 1000.0;
    let recipe = request.get("recipe").ok_or("Missing complete recipe")?;
    if recipe["version"] != 1 || !recipe["cameraRaw"].is_object() {
        return Err("Unsupported RAW recipe".into());
    }
    let mut p: CameraRaw = serde_json::from_value(recipe["cameraRaw"].clone()).map_err(|e| e.to_string())?;
    p.validate_changes(&CameraRaw::default())?;
    // The raw stage: white balance, exposure, Shadows, Highlights, Clarity, Texture, Dehaze (as `frame` develops them).
    let mut options = src.options.clone();
    options.exposure = p.exposure as f64;
    options.tone = photocraft_io::raw::Tone { shadows: p.shadows, highlights: p.highlights, clarity: p.clarity, texture: p.texture, dehaze: p.dehaze };
    if let Some(wb) = src.sensor.camera_wb {
        let gains = photocraft_algo::camera_raw::white_balance_gains(p.temperature, p.tint);
        options.white_balance =
            photocraft_raw::WhiteBalance::Multipliers([wb[0] * gains[0] as f64, wb[1] * gains[1] as f64, wb[2] * gains[2] as f64]);
    }
    p.encoding_gamma = 1.8;
    (p.exposure, p.shadows, p.highlights, p.clarity, p.texture, p.dehaze) = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
    if src.sensor.camera_wb.is_some() {
        p.temperature = 0.0;
        p.tint = 0.0;
    }
    let full = src.crop;
    let o = src.sensor.orientation;
    let (fw, fh) = if (5..=8).contains(&o) { (full.height, full.width) } else { (full.width, full.height) };

    // What the whole photo measures, once per recipe.
    let key = recipe["cameraRaw"].to_string();
    let mut measure_ms = 0.0;
    if src.stats.as_ref().is_none_or(|(k, _, _)| *k != key) {
        let measuring = Instant::now();
        let sensor = src.measured.as_ref().unwrap_or(&src.sensor);
        let reduced = photocraft_raw::develop_reduced(sensor, &options, MEASURE_EDGE)
            .or_else(|_| photocraft_raw::develop_sensor(sensor, &options).map(|developed| photocraft_raw::ReducedDeveloped {
                full_width: developed.width,
                full_height: developed.height,
                developed,
            }))
            .map_err(|e| e.to_string())?;
        let d = &reduced.developed;
        let (mw, mh) = (d.width as usize, d.height as usize);
        let mut px: Vec<[f32; 4]> =
            d.rgb.as_chunks::<3>().0.iter().map(|q| [q[0] as f32 / 65535.0, q[1] as f32 / 65535.0, q[2] as f32 / 65535.0, 1.0]).collect();
        let mut q = p.clone();
        q.pixel_scale = (mw.max(mh) as f32 / fw.max(fh) as f32).min(1.0);
        let whole = photocraft_algo::camera_raw::measure(&mut px, mw, mh, &q, false);
        src.stats = Some((key, d.tone_stats.clone(), Arc::new(whole)));
        measure_ms = measuring.elapsed().as_secs_f64() * 1000.0;
    }
    let (_, tone_stats, whole) = src.stats.clone().ok_or("No statistics")?;

    // The region, with the margin Camera Raw's spatial filters reach into, developed as a window of the whole photo.
    let region = sensor_rect(o, full, [rect[0], rect[1], rect[2], rect[3]]);
    p.pixel_scale = 1.0;
    let m = photocraft_algo::camera_raw::window_margin(&p, fw, fh);
    let (rx, ry) = (region.x - full.x, region.y - full.y);
    let (ex0, ey0) = (rx.saturating_sub(m), ry.saturating_sub(m));
    let (ex1, ey1) = ((rx + region.width + m).min(full.width), (ry + region.height + m).min(full.height));
    let expanded = photocraft_raw::Rect::new(full.x + ex0, full.y + ey0, ex1 - ex0, ey1 - ey0);
    options.window = Some(photocraft_raw::RawWindow { x: ex0, y: ey0, full_w: full.width, full_h: full.height, stats: tone_stats });
    src.sensor.crop = expanded;
    let developed = photocraft_raw::develop_sensor(&src.sensor, &options);
    src.sensor.crop = full;
    let developed = developed.map_err(|e| e.to_string())?;
    let raw_ms = start.elapsed().as_secs_f64() * 1000.0 - open_ms - measure_ms;
    let (dw, dh) = (developed.width as usize, developed.height as usize);
    let (ox, oy, ow, oh) = oriented_rect(o, (full.width, full.height), (ex0, ey0, expanded.width, expanded.height));
    if (ow, oh) != (dw, dh) {
        return Err(format!("the region developed at {dw} x {dh}, not {ow} x {oh}"));
    }
    let mut px: Vec<[f32; 4]> =
        developed.rgb.as_chunks::<3>().0.iter().map(|q| [q[0] as f32 / 65535.0, q[1] as f32 / 65535.0, q[2] as f32 / 65535.0, 1.0]).collect();
    drop(developed);
    let camera = Instant::now();
    p.window = Some(photocraft_algo::camera_raw::Window { x: ox, y: oy, full_w: fw, full_h: fh, stats: Some(whole) });
    develop(&mut px, dw, dh, &p, false);
    let camera_ms = camera.elapsed().as_secs_f64() * 1000.0;
    // The margin cut off.
    let (ix, iy, w, h) = oriented_rect(o, (full.width, full.height), (rx, ry, region.width, region.height));
    let (cx, cy) = (ix - ox, iy - oy);
    let mut shown = vec![0.0f32; w * h * 4];
    shown.par_chunks_mut(w * 4).enumerate().for_each(|(r, dst)| {
        let row = &px[(cy + r) * dw + cx..(cy + r) * dw + cx + w];
        display.convert_f32(row.as_flattened(), 4, dst, 4, true);
    });
    let bytes: Vec<u8> = shown.iter().map(|v| (v.clamp(0.0, 1.0) * 255.0 + 0.5) as u8).collect();
    Ok((
        json!({"ok":true,"protocol":2,"width":w,"height":h,"bytes":bytes.len(),"fullSize":[fw,fh],
               "region":[rx, ry, region.width, region.height],"margin":m,
               "shown":[ix as f64 / fw as f64, iy as f64 / fh as f64, (ix + w) as f64 / fw as f64, (iy + h) as f64 / fh as f64],
               "openMs":open_ms,"measureMs":measure_ms,"rawMs":raw_ms,"cameraRawMs":camera_ms,
               "workerMs":start.elapsed().as_secs_f64()*1000.0}),
        bytes,
    ))
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
    let mut regions: Option<RegionSource> = None;
    let region_display = photocraft_cms::cached(
        Builtin::ProPhotoCompat.profile(),
        Builtin::Srgb.profile(),
        TransformOptions { precise_float: false, ..Default::default() },
    )
    .map_err(|e| e.to_string())?;
    let mut opens = 0;
    let mut output = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let line = line.map_err(|e| e.to_string())?;
        let result = (|| -> Result<(Value, Vec<u8>)> {
            let request: Value = serde_json::from_str(&line).map_err(|e| e.to_string())?;
            if request["op"] == "hello" {
                Ok((json!({"ok":true,"protocol":2,"redEye":1,"sky":2,"patch":1,"denoise":2,"superres":1,"region":2,"bytes":0}), vec![]))
            } else if request["op"] == "open" {
                let start = Instant::now();
                source = None;
                opens += 1;
                source = Some(open(&request, opens)?);
                let mut result = frame(source.as_mut().ok_or("No source")?, &request, &mut gpu)?;
                result.0["openMs"] = json!(start.elapsed().as_secs_f64() * 1000.0);
                Ok(result)
            } else if request["op"] == "denoiseInput" {
                denoise_input(&request)
            } else if request["op"] == "superresInput" {
                superres_input(&request)
            } else if request["op"] == "regionRender" {
                region_render(&mut regions, &request, &region_display)
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
