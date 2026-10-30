//! 32-bit float WAV, written as it comes and read back whole.

use std::fs::{File, OpenOptions};
use std::io::{self, BufWriter, Read, Seek, SeekFrom, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

const HEADER: usize = 58;
const FACT_FRAMES_AT: u64 = 46;
const DATA_SIZE_AT: u64 = 54;

/// Writes the header with the sizes at zero; [`WavWriter::patch`] and
/// [`WavWriter::finish`] fill them in, so a file cut off reads up to the last patch.
pub struct WavWriter {
    file: BufWriter<File>,
    channels: u16,
    samples: u64,
}

fn header(rate: u32, channels: u16, samples: u64) -> io::Result<[u8; HEADER]> {
    let data = u32::try_from(samples * 4)
        .ok()
        .filter(|d| *d <= u32::MAX - HEADER as u32)
        .ok_or_else(|| io::Error::other("a WAV file holds at most 4 GiB"))?;
    let frames = (samples / channels as u64) as u32;
    let mut h = [0u8; HEADER];
    let mut at = 0;
    let mut put = |bytes: &[u8]| {
        h[at..at + bytes.len()].copy_from_slice(bytes);
        at += bytes.len();
    };
    put(b"RIFF");
    put(&(HEADER as u32 - 8 + data).to_le_bytes());
    put(b"WAVE");
    put(b"fmt ");
    put(&18u32.to_le_bytes());
    put(&3u16.to_le_bytes());
    put(&channels.to_le_bytes());
    put(&rate.to_le_bytes());
    put(&(rate * 4 * channels as u32).to_le_bytes());
    put(&(4 * channels).to_le_bytes());
    put(&32u16.to_le_bytes());
    put(&0u16.to_le_bytes());
    put(b"fact");
    put(&4u32.to_le_bytes());
    put(&frames.to_le_bytes());
    put(b"data");
    put(&data.to_le_bytes());
    Ok(h)
}

impl WavWriter {
    pub fn create(path: &Path, rate: u32, channels: u16) -> io::Result<WavWriter> {
        if channels == 0 {
            return Err(io::Error::other("a WAV file needs a channel"));
        }
        let created = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)?;
        let mut file = BufWriter::new(created);
        file.write_all(&header(rate, channels, 0)?)?;
        Ok(WavWriter {
            file,
            channels,
            samples: 0,
        })
    }

    /// Interleaved samples.
    pub fn write(&mut self, samples: &[f32]) -> io::Result<()> {
        header(0, self.channels, self.samples + samples.len() as u64)?;
        for s in samples {
            self.file.write_all(&s.to_le_bytes())?;
        }
        self.samples += samples.len() as u64;
        Ok(())
    }

    pub fn write_silence(&mut self, frames: u64) -> io::Result<()> {
        let zeros = [0.0f32; 256];
        let mut left = frames * self.channels as u64;
        while left > 0 {
            let n = left.min(zeros.len() as u64) as usize;
            self.write(&zeros[..n])?;
            left -= n as u64;
        }
        Ok(())
    }

    pub fn frames(&self) -> u64 {
        self.samples / self.channels as u64
    }

    /// The sizes in the header brought up to what is written.
    pub fn patch(&mut self) -> io::Result<()> {
        let h = header(0, self.channels, self.samples)?;
        self.file.flush()?;
        let file = self.file.get_mut();
        file.seek(SeekFrom::Start(4))?;
        file.write_all(&h[4..8])?;
        file.seek(SeekFrom::Start(FACT_FRAMES_AT))?;
        file.write_all(&h[46..50])?;
        file.seek(SeekFrom::Start(DATA_SIZE_AT))?;
        file.write_all(&h[54..58])?;
        file.seek(SeekFrom::End(0))?;
        file.flush()
    }

    pub fn finish(mut self) -> io::Result<()> {
        self.patch()?;
        self.file.get_ref().sync_all()
    }
}

#[derive(Debug)]
pub struct Wav {
    pub rate: u32,
    pub channels: u16,
    /// Interleaved.
    pub samples: Vec<f32>,
}

/// A 32-bit float WAV file, whatever chunks it has besides fmt and data.
pub fn read(path: &Path) -> io::Result<Wav> {
    let bad = |what: &str| io::Error::new(io::ErrorKind::InvalidData, what.to_string());
    let mut bytes = Vec::new();
    File::open(path)?.read_to_end(&mut bytes)?;
    if bytes.len() < 12 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err(bad("not a WAV file"));
    }
    let u16_at = |at: usize| u16::from_le_bytes([bytes[at], bytes[at + 1]]);
    let u32_at = |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap());
    let mut format = None;
    let mut at = 12;
    while at + 8 <= bytes.len() {
        let id = &bytes[at..at + 4];
        let size = u32_at(at + 4) as usize;
        let body = at + 8;
        let end = body.saturating_add(size).min(bytes.len());
        if id == b"fmt " {
            if size < 16 || end < body + 16 {
                return Err(bad("a WAV fmt chunk too short"));
            }
            format = Some((
                u16_at(body),
                u16_at(body + 2),
                u32_at(body + 4),
                u16_at(body + 14),
            ));
        } else if id == b"data" {
            let (tag, channels, rate, bits) = format.ok_or_else(|| bad("data before fmt"))?;
            if tag != 3 || bits != 32 || channels == 0 {
                return Err(bad("not a 32-bit float WAV file"));
            }
            let samples = bytes[body..end]
                .chunks_exact(4)
                .map(|c| f32::from_le_bytes(c.try_into().unwrap()))
                .collect();
            return Ok(Wav {
                rate,
                channels,
                samples,
            });
        }
        at = body + size + (size & 1);
    }
    Err(bad("a WAV file without data"))
}
