//! Temporary integration test for e0569ef0. Assertions confirm audited failures.
//! Copy to photocraft/crates/ui-egui/tests/audit_20261014.rs and run:
//! cargo test -j 4 -p photocraft-ui-egui --test audit_20261014 -- --nocapture
use std::sync::{Arc, Mutex};
use photocraft_ui_egui::{PhotocraftApp, Services, camera_raw_ui, control, jobs_ui, menus, raw_workspace};
use serde_json::{Value, json};

fn raw() -> Vec<u8> {
    use photocraft_raw::testgen::{DngSpec, mosaic, scene};
    let (w, h) = (48, 32);
    let mut spec = DngSpec::cfa(w, h, mosaic(&scene(w, h), w, [0, 1, 1, 2], 0, 30000));
    spec.as_shot_neutral = Some([0.5, 1.0, 0.7]);
    spec.build()
}

fn call(app: &mut PhotocraftApp, ctx: &egui::Context, method: &str, params: Value) -> Value {
    let (req, _) = control::ControlRequest::new(method, params);
    match control::handle(app, ctx, &req) {
        control::Outcome::Done(value) => value,
        control::Outcome::AfterJob(job) => {
            let result = app.session.wait_job(job).unwrap();
            jobs_ui::tick(app, ctx);
            json!({"ok":true,"result":result})
        }
        _ => panic!("unexpected asynchronous input"),
    }
}

fn open(app: &mut PhotocraftApp, ctx: &egui::Context, extra: Value) {
    assert_eq!(call(app, ctx, "app.open", json!({"path":"IMG.dng", "replace":true,"cameraRaw":extra}))["ok"], true);
    let mut output = ctx.run_ui(egui::RawInput::default(), |ui| camera_raw_ui::show(app, ui.ctx()));
    output.textures_delta.clear();
    assert_eq!(raw_workspace::control(app, ctx, &json!({})).unwrap()["target"], "openRaw");
}

#[test]
fn a_failed_recipe_write_still_reports_success_and_drops_the_adjustment() {
    let bytes = raw();
    let attempted = Arc::new(Mutex::new(Vec::new()));
    let recorded = attempted.clone();
    let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), Services {
        background_automation_read: Some(Arc::new(move |_| Ok(("IMG.dng".into(), bytes.clone())))),
        background_automation_write: Some(Arc::new(move |path, _| {
            recorded.lock().unwrap().push(path.to_string());
            Err("disk full".into())
        })),
        ..Default::default()
    });
    app.background_jobs = true;
    app.raw_workspace.enabled = true;
    let ctx = egui::Context::default();
    PhotocraftApp::setup_context(&ctx, Default::default());
    open(&mut app, &ctx, json!({"recipePath":"IMG.dng.cameraraw.json"}));
    menus::invoke(&mut app, &ctx, "filter.cameraRaw", json!({"ui":{"set":{"exposure":1.0}}})).unwrap();
    let reply = raw_workspace::control(&mut app, &ctx, &json!({"done":true})).unwrap();
    assert_eq!(reply["open"], false);
    assert_eq!(reply["result"]["changed"], true);
    assert_eq!(attempted.lock().unwrap().len(), 1);
    assert_eq!(raw_workspace::control(&mut app, &ctx, &json!({})).unwrap()["open"], false);
    open(&mut app, &ctx, json!({"recipePath":"IMG.dng.cameraraw.json"}));
    assert!(raw_workspace::control(&mut app, &ctx, &json!({})).unwrap()["recipe"]["cameraRaw"].get("exposure").is_none());
    println!("CONFIRMED: failed recipe write returns success, closes the dialog, and the next visit loses exposure=1");
}

#[test]
fn native_save_loses_the_host_binding_after_redevelop_and_after_continue() {
    for continued in [false, true] {
        let bytes = raw();
        let saved = photocraft_doc::Document::new("saved", photocraft_geom::Size::new(8, 8),
            photocraft_color::ColorMode::Rgb, photocraft_color::SampleType::U8);
        let project = photocraft_io::export(&saved, "IMG.dng.pcraft", &Default::default()).unwrap().bytes;
        let writes = Arc::new(Mutex::new(Vec::new()));
        let into = writes.clone();
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), Services {
            background_automation_read: Some(Arc::new(move |path| {
                Ok((path.to_string(), if path.ends_with(".pcraft") { project.clone() } else { bytes.clone() }))
            })),
            background_automation_write: Some(Arc::new(move |path, _| { into.lock().unwrap().push(path.to_string()); Ok(()) })),
            file_dialog: Some(Box::new(|_, _, reply| reply.send(None))),
            ..Default::default()
        });
        app.background_jobs = true;
        app.raw_workspace.enabled = true;
        let ctx = egui::Context::default();
        PhotocraftApp::setup_context(&ctx, Default::default());
        let spec = if continued { json!({"continueFrom":"IMG.dng.pcraft"}) } else { json!({}) };
        open(&mut app, &ctx, spec);
        let old = app.session.active().unwrap().doc.id;
        assert_eq!(call(&mut app, &ctx, "app.bind", json!({"path":"IMG.dng.pcraft","preview":"IMG.dng.photocraft.png"}))["ok"], true);
        if !continued {
            menus::invoke(&mut app, &ctx, "filter.cameraRaw", json!({"ui":{"set":{"exposure":1.0}}})).unwrap();
        }
        let value = raw_workspace::control(&mut app, &ctx, &json!({"commit":true})).unwrap();
        if let Some(job) = value.get("result").and_then(|r| r.get("job")).and_then(Value::as_u64) {
            app.session.wait_job(photocraft_engine::jobs::JobId(job)).unwrap();
            jobs_ui::tick(&mut app, &ctx);
        }
        assert_ne!(old, app.session.active().unwrap().doc.id);
        app.session.execute("layer.new.layer", json!({})).unwrap();
        let result = menus::invoke(&mut app, &ctx, "file.save", json!({}));
        assert!(writes.lock().unwrap().is_empty(), "hosted Save must have been bypassed");
        println!("CONFIRMED: continued={continued}, document id changed, native Save bypasses the host: {result:?}");
    }
}

#[test]
fn recipe_display_uses_different_shadow_and_highlight_math_from_the_dialog() {
    let bytes = raw();
    let doc = photocraft_io::raw::import_raw("IMG.dng", &bytes).unwrap().document;
    let params = photocraft_algo::camera_raw::CameraRaw { shadows: 75.0, highlights: -70.0, ..Default::default() };
    let saved = photocraft_ui_egui::raw_recipe::render_display(&doc, &params).unwrap();
    // Mirror CameraRawDialog::render's raw-stage pass using the same composited pixels.
    let mut buffer = photocraft_compose::thumbnail_buffer(&doc, 2560);
    let (w, h) = (buffer.rect.width() as usize, buffer.rect.height() as usize);
    let curve = photocraft_io::raw::tone_curve(&bytes);
    photocraft_io::raw::retone(&mut buffer.px, w, h, &curve,
        photocraft_io::raw::Tone { shadows:75.0, highlights:-70.0, ..Default::default() });
    let mut rest = params.clone();
    rest.shadows = 0.0;
    rest.highlights = 0.0;
    rest.pixel_scale = (w as f32 / doc.size.width.max(1) as f32).min(1.0);
    photocraft_algo::camera_raw::develop(&mut buffer.px, w, h, &rest, false);
    let expected = photocraft_io::display_jpeg_from_buffer(&buffer, doc.icc_profile.as_ref().map(|i| i.to_vec()), 90).unwrap().bytes;
    let a = photocraft_codecs::decode(&saved).unwrap();
    let b = photocraft_codecs::decode(&expected).unwrap();
    let difference: f64 = a.data().iter().zip(b.data()).map(|(x, y)| f64::from(x.abs_diff(*y))).sum::<f64>() / a.data().len() as f64;
    assert!(difference > 5.0, "saved render should demonstrate a material difference, got {difference}");
    println!("CONFIRMED: recipe JPEG differs from the dialog's raw-stage rendering by mean {difference:.2}/255 RGB codes");
}

#[test]
fn native_save_writes_no_display_jpeg() {
    let writes = Arc::new(Mutex::new(Vec::new()));
    let into = writes.clone();
    let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), Services {
        background_automation_write: Some(Arc::new(move |path, _| { into.lock().unwrap().push(path.to_string()); Ok(()) })),
        ..Default::default()
    });
    app.session.add_document(photocraft_doc::Document::new("IMG.png", photocraft_geom::Size::new(8, 8),
        photocraft_color::ColorMode::Rgb, photocraft_color::SampleType::U8), None);
    let ctx = egui::Context::default();
    assert_eq!(call(&mut app, &ctx, "app.bind", json!({"path":"IMG.png.pcraft","preview":"IMG.png.photocraft.png"}))["ok"], true);
    let result = menus::invoke(&mut app, &ctx, "file.save", json!({})).unwrap();
    if let Some(job) = result.get("job").and_then(Value::as_u64) {
        app.session.wait_job(photocraft_engine::jobs::JobId(job)).unwrap();
    }
    assert_eq!(writes.lock().unwrap().len(), 2);
    assert!(writes.lock().unwrap().iter().all(|path| !path.ends_with(".jpg")));
    println!("CONFIRMED: native Save refreshes the project and PNG, but never the display JPEG");
}
