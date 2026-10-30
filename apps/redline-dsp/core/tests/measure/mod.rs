//! Signals and measurements shared by the dynamics and colour tests.
#![allow(dead_code)]

use redline_dsp_core::effect::{make, Effect};
use redline_dsp_core::pitch::Yin;
use std::f64::consts::PI;

pub const RATE: f32 = 48000.0;

pub fn effect(name: &str, params: &[(&str, f32)]) -> Box<dyn Effect> {
    let mut e = make(name).unwrap_or_else(|| panic!("no effect {name}"));
    for (p, v) in params {
        e.set(p, *v).unwrap();
    }
    e.reset(RATE);
    e
}

pub fn at(seconds: f32) -> usize {
    (seconds * RATE).round() as usize
}

fn wave(n: usize, f: impl Fn(f64) -> f32) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let v = f(i as f64 / RATE as f64);
            [v, v]
        })
        .collect()
}

pub fn sine(freq: f32, amp: f32, n: usize) -> Vec<[f32; 2]> {
    wave(n, |t| amp * (2.0 * PI * freq as f64 * t).sin() as f32)
}

/// A square of exactly ±amp: its peak and its RMS are the same.
pub fn square(freq: f32, amp: f32, n: usize) -> Vec<[f32; 2]> {
    wave(n, |t| {
        if (freq as f64 * t).fract() < 0.5 {
            amp
        } else {
            -amp
        }
    })
}

pub fn saw(freq: f32, amp: f32, n: usize) -> Vec<[f32; 2]> {
    wave(n, |t| amp * (2.0 * (freq as f64 * t).fract() - 1.0) as f32)
}

/// A saw built from its harmonics up to 10 kHz: no aliasing.
pub fn clean_saw(freq: f32, amp: f32, n: usize) -> Vec<[f32; 2]> {
    let top = (10000.0 / freq) as usize;
    wave(n, |t| {
        let s: f64 = (1..=top)
            .map(|k| (2.0 * PI * k as f64 * freq as f64 * t).sin() / k as f64)
            .sum();
        amp * (2.0 / PI * s) as f32
    })
}

/// Uniform white noise in ±amp, the same for a seed.
pub fn noise(n: usize, amp: f32, seed: u64) -> Vec<[f32; 2]> {
    let mut s = seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1;
    let mut next = move || {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        (s >> 40) as f32 / (1u64 << 23) as f32 - 1.0
    };
    (0..n).map(|_| [amp * next(), amp * next()]).collect()
}

pub fn add(a: &[[f32; 2]], b: &[[f32; 2]]) -> Vec<[f32; 2]> {
    a.iter()
        .zip(b)
        .map(|(x, y)| [x[0] + y[0], x[1] + y[1]])
        .collect()
}

/// Each frame scaled by `gain(index)`.
pub fn shaped(x: &[[f32; 2]], gain: impl Fn(usize) -> f32) -> Vec<[f32; 2]> {
    x.iter()
        .enumerate()
        .map(|(i, f)| [f[0] * gain(i), f[1] * gain(i)])
        .collect()
}

pub fn run(e: &mut dyn Effect, x: &[[f32; 2]], block: usize) -> Vec<[f32; 2]> {
    let mut y = x.to_vec();
    for chunk in y.chunks_mut(block) {
        e.process(chunk);
    }
    y
}

/// With the side signal handed over before each block.
pub fn run_side(
    e: &mut dyn Effect,
    x: &[[f32; 2]],
    side: &[[f32; 2]],
    block: usize,
) -> Vec<[f32; 2]> {
    let mut y = x.to_vec();
    for (chunk, s) in y.chunks_mut(block).zip(side.chunks(block)) {
        e.sidechain(s);
        e.process(chunk);
    }
    y
}

pub fn channel(y: &[[f32; 2]], ch: usize) -> Vec<f32> {
    y.iter().map(|f| f[ch]).collect()
}

pub fn peak(x: &[f32]) -> f32 {
    x.iter().fold(0.0, |m, v| m.max(v.abs()))
}

pub fn rms(x: &[f32]) -> f32 {
    (x.iter().map(|v| (*v as f64).powi(2)).sum::<f64>() / x.len().max(1) as f64).sqrt() as f32
}

pub fn db(ratio: f32) -> f32 {
    20.0 * ratio.log10()
}

/// The amplitude of the sinusoid at `freq` in x: a Hann-windowed DFT bin.
pub fn amp_at(x: &[f32], freq: f32) -> f32 {
    let n = x.len();
    let w = 2.0 * PI * freq as f64 / RATE as f64;
    let (mut re, mut im, mut sum) = (0.0f64, 0.0f64, 0.0f64);
    for (i, &v) in x.iter().enumerate() {
        let hann = 0.5 - 0.5 * (2.0 * PI * i as f64 / n as f64).cos();
        re += hann * v as f64 * (w * i as f64).cos();
        im -= hann * v as f64 * (w * i as f64).sin();
        sum += hann;
    }
    (2.0 * re.hypot(im) / sum) as f32
}

/// The frequency of the strongest component between `low` and `high`, in steps of `step` Hz.
pub fn strongest(x: &[f32], low: f32, high: f32, step: f32) -> f32 {
    let mut best = (0.0, low);
    let mut f = low;
    while f <= high {
        let a = amp_at(x, f);
        if a > best.0 {
            best = (a, f);
        }
        f += step;
    }
    best.1
}

/// Every YIN detection over x: Some(Hz) or None for no clear pitch.
pub fn pitches(x: &[f32]) -> Vec<Option<f32>> {
    let mut yin = Yin::new();
    yin.reset(RATE);
    let mut out = Vec::new();
    for &v in x {
        if yin.push(v) {
            out.push(yin.freq());
        }
    }
    out
}

/// The share of detections within `cents` of `freq`.
pub fn share_at(found: &[Option<f32>], freq: f32, cents: f32) -> f32 {
    let near = found
        .iter()
        .filter(|f| f.is_some_and(|f| (1200.0 * (f / freq).log2()).abs() <= cents))
        .count();
    near as f32 / found.len().max(1) as f32
}
