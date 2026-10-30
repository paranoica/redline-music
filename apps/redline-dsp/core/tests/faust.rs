//! The committed Rust of the Faust reverbs is what core/faust/generate.sh makes from the .dsp
//! files, and holds only what the zeroed allocation in src/faust/mod.rs allows.

use std::path::{Path, PathBuf};
use std::process::Command;

const GENERATED: &[&str] = &["room", "plate", "hall", "shimmer"];

fn core() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn committed(name: &str) -> String {
    std::fs::read_to_string(core().join("src/faust").join(format!("{name}.rs"))).unwrap()
}

#[test]
fn every_dsp_file_has_its_generated_rust_and_no_other() {
    let mut dsp: Vec<String> = std::fs::read_dir(core().join("faust"))
        .unwrap()
        .filter_map(|e| {
            let p = e.unwrap().path();
            (p.extension()? == "dsp").then(|| p.file_stem().unwrap().to_string_lossy().into())
        })
        .collect();
    dsp.sort();
    let mut want: Vec<String> = GENERATED.iter().map(|s| s.to_string()).collect();
    want.sort();
    assert_eq!(dsp, want);
}

#[test]
fn the_generated_structs_hold_only_numbers() {
    for name in GENERATED {
        let text = committed(name);
        assert!(text.starts_with(&format!(
            "// Generated from core/faust/{name}.dsp by core/faust/generate.sh"
        )));
        let start = text.find("pub struct").expect("a struct");
        let body = &text[text[start..].find('{').unwrap() + start + 1..];
        let body = &body[..body.find('}').unwrap()];
        for field in body.lines().map(str::trim).filter(|l| !l.is_empty()) {
            let ty = field
                .split_once(':')
                .expect(field)
                .1
                .trim()
                .trim_end_matches(',');
            let plain = ["F32", "i32", "FaustFloat"];
            let ok = plain.contains(&ty)
                || ty
                    .strip_prefix('[')
                    .and_then(|t| t.split_once(';'))
                    .is_some_and(|(t, _)| plain.contains(&t));
            assert!(ok, "{name}: {field}");
        }
    }
}

fn faust_installed() -> bool {
    Command::new("faust")
        .arg("--version")
        .output()
        .is_ok_and(|o| o.status.success())
}

#[test]
fn regenerating_the_faust_code_changes_nothing() {
    if !faust_installed() {
        eprintln!("faust is not installed: the generated code is not checked against it");
        return;
    }
    let out = Path::new(env!("CARGO_TARGET_TMPDIR")).join("faust-regenerated");
    std::fs::create_dir_all(&out).unwrap();
    let status = Command::new("bash")
        .arg(core().join("faust/generate.sh"))
        .arg(&out)
        .status()
        .unwrap();
    assert!(status.success());
    for name in GENERATED {
        let fresh = std::fs::read_to_string(out.join(format!("{name}.rs"))).unwrap();
        assert!(
            fresh == committed(name),
            "src/faust/{name}.rs differs from what core/faust/generate.sh makes now; rerun it"
        );
    }
}
