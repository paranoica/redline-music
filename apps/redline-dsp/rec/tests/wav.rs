use redline_rec::wav::{read, WavWriter};
use std::path::PathBuf;

fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("rec-wav");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    // a run cut short leaves its file, and a take is never written over another
    let _ = std::fs::remove_file(&path);
    path
}

fn u16_at(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}

fn u32_at(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}

#[test]
fn a_closed_file_has_the_float_wav_header_with_its_sizes() {
    let path = scratch("header.wav");
    let mut w = WavWriter::create(&path, 48000, 1).unwrap();
    w.write(&[0.5, -0.25]).unwrap();
    w.write(&[1.0]).unwrap();
    assert_eq!(w.frames(), 3);
    w.finish().unwrap();
    let b = std::fs::read(&path).unwrap();
    std::fs::remove_file(&path).unwrap();
    // RIFF, a WAVE_FORMAT_IEEE_FLOAT fmt chunk of 18 bytes, a fact chunk, then the data
    assert_eq!(b.len(), 12 + 26 + 12 + 8 + 12);
    assert_eq!(&b[0..4], b"RIFF");
    assert_eq!(u32_at(&b, 4) as usize, b.len() - 8);
    assert_eq!(&b[8..12], b"WAVE");
    assert_eq!(&b[12..16], b"fmt ");
    assert_eq!(u32_at(&b, 16), 18);
    assert_eq!(u16_at(&b, 20), 3, "IEEE float");
    assert_eq!(u16_at(&b, 22), 1, "channels");
    assert_eq!(u32_at(&b, 24), 48000);
    assert_eq!(u32_at(&b, 28), 48000 * 4, "bytes a second");
    assert_eq!(u16_at(&b, 32), 4, "block align");
    assert_eq!(u16_at(&b, 34), 32, "bits");
    assert_eq!(u16_at(&b, 36), 0, "no extension");
    assert_eq!(&b[38..42], b"fact");
    assert_eq!(u32_at(&b, 42), 4);
    assert_eq!(u32_at(&b, 46), 3, "frames");
    assert_eq!(&b[50..54], b"data");
    assert_eq!(u32_at(&b, 54), 12);
    let samples: Vec<f32> = b[58..]
        .chunks(4)
        .map(|c| f32::from_le_bytes(c.try_into().unwrap()))
        .collect();
    assert_eq!(samples, [0.5, -0.25, 1.0]);
}

#[test]
fn a_stereo_file_reads_back_as_written_and_an_empty_one_as_empty() {
    let path = scratch("stereo.wav");
    let mut w = WavWriter::create(&path, 44100, 2).unwrap();
    let x: Vec<f32> = (0..1000).map(|i| (i as f32 * 0.01).sin()).collect();
    w.write(&x).unwrap();
    w.write_silence(5).unwrap();
    assert_eq!(w.frames(), 505);
    w.finish().unwrap();
    let wav = read(&path).unwrap();
    assert_eq!((wav.rate, wav.channels), (44100, 2));
    assert_eq!(&wav.samples[..1000], &x[..]);
    assert_eq!(&wav.samples[1000..], &[0.0; 10]);

    std::fs::remove_file(&path).unwrap();
    let path = scratch("empty.wav");
    WavWriter::create(&path, 48000, 1)
        .unwrap()
        .finish()
        .unwrap();
    let wav = read(&path).unwrap();
    std::fs::remove_file(&path).unwrap();
    assert!(wav.samples.is_empty());
}

#[test]
fn a_file_cut_off_before_close_still_reads_up_to_the_last_patch() {
    let path = scratch("cut.wav");
    let mut w = WavWriter::create(&path, 48000, 1).unwrap();
    w.write(&[0.1; 100]).unwrap();
    w.patch().unwrap();
    w.write(&[0.2; 50]).unwrap();
    // the process dies: no finish, the header says what the last patch saw
    std::mem::forget(w);
    let wav = read(&path).unwrap();
    std::fs::remove_file(&path).unwrap();
    assert_eq!(wav.samples, vec![0.1; 100]);
}

#[test]
fn reading_refuses_what_is_not_a_float_wav() {
    let path = scratch("bad.wav");
    std::fs::write(&path, b"RIFF\x04\x00\x00\x00WAVX").unwrap();
    assert!(read(&path).is_err());
    let mut pcm = std::fs::read({
        let p = scratch("pcm-src.wav");
        WavWriter::create(&p, 48000, 1).unwrap().finish().unwrap();
        p
    })
    .unwrap();
    pcm[20] = 1; // PCM
    std::fs::write(&path, &pcm).unwrap();
    let err = read(&path).unwrap_err().to_string();
    std::fs::remove_file(&path).unwrap();
    assert!(err.contains("float"), "{err}");
}

#[test]
fn a_take_is_created_for_the_owner_only_and_never_over_another() {
    use std::os::unix::fs::PermissionsExt;
    let path = scratch("owner.wav");
    let w = WavWriter::create(&path, 48000, 1).unwrap();
    w.finish().unwrap();
    let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
    let again = WavWriter::create(&path, 48000, 1);
    std::fs::remove_file(&path).unwrap();
    assert_eq!(mode, 0o600);
    assert_eq!(
        again.err().map(|e| e.kind()),
        Some(std::io::ErrorKind::AlreadyExists)
    );
}
