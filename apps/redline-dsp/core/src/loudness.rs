//! Integrated loudness by ITU-R BS.1770-4, step for step as pyloudnorm measures it.

use crate::filters::{flushing_tiny, lfilter};

/// One K-weighting stage: its coefficients and the gain applied after it.
pub struct Stage {
    pub b: Vec<f64>,
    pub a: Vec<f64>,
    pub gain: f64,
}

const CHANNEL_GAINS: [f64; 5] = [1.0, 1.0, 1.0, 1.41, 1.41];
const ABSOLUTE_GATE: f64 = -70.0;

fn level(z: &[f64]) -> f64 {
    -0.691
        + 10.0
            * z.iter()
                .zip(CHANNEL_GAINS)
                .map(|(v, g)| g * v)
                .sum::<f64>()
                .log10()
}

/// Running sums of the squares of one channel's K-weighted signal, from a leading zero.
///
/// The weighted signal is kept in single precision between stages, as pyloudnorm keeps it.
pub fn weighted_sums(ch: &[f32], stages: &[Stage]) -> Vec<f64> {
    flushing_tiny(|| {
        let weighted = stages.iter().fold(ch.to_vec(), |x, s| {
            lfilter(&s.b, &s.a, &x)
                .into_iter()
                .map(|v| (s.gain * v) as f32)
                .collect()
        });
        let mut sums = Vec::with_capacity(weighted.len() + 1);
        let mut total = 0.0;
        sums.push(total);
        for v in weighted {
            total += (v as f64) * (v as f64);
            sums.push(total);
        }
        sums
    })
}

/// Gated loudness of the channels, in LUFS; -inf when no block passes the gates.
pub fn integrated(
    channels: &[Vec<f32>],
    rate: f64,
    stages: &[Stage],
    block: f64,
    overlap: f64,
) -> f64 {
    let sums: Vec<Vec<f64>> = channels
        .iter()
        .map(|ch| weighted_sums(ch, stages))
        .collect();
    gated(&sums, rate, block, overlap)
}

/// Gated loudness, in LUFS, from every channel's [`weighted_sums`].
pub fn gated(sums: &[Vec<f64>], rate: f64, block: f64, overlap: f64) -> f64 {
    let n = (sums[0].len() - 1) as f64;
    let step = 1.0 - overlap;
    let blocks = (((n / rate - block) / (block * step)).round_ties_even() + 1.0) as usize;
    let scale = 1.0 / (block * rate);
    // z[j][i]: mean square of channel i in block j, its bounds computed as pyloudnorm does
    let z: Vec<Vec<f64>> = (0..blocks)
        .map(|j| {
            let lo = (block * (j as f64 * step) * rate) as usize;
            let hi = (block * (j as f64 * step + 1.0) * rate) as usize;
            sums.iter()
                .map(|s| {
                    let hi = hi.min(s.len() - 1);
                    scale * (s[hi] - s[lo.min(hi)])
                })
                .collect()
        })
        .collect();
    let levels: Vec<f64> = z.iter().map(|zj| level(zj)).collect();
    let mean_over = |keep: &dyn Fn(f64) -> bool| -> Vec<f64> {
        let picked: Vec<usize> = (0..blocks).filter(|&j| keep(levels[j])).collect();
        (0..sums.len())
            .map(|i| {
                if picked.is_empty() {
                    f64::NAN
                } else {
                    picked.iter().map(|&j| z[j][i]).sum::<f64>() / picked.len() as f64
                }
            })
            .collect()
    };
    let relative = level(&mean_over(&|l| l >= ABSOLUTE_GATE)) - 10.0;
    let gated: Vec<f64> = mean_over(&|l| l > relative && l > ABSOLUTE_GATE)
        .into_iter()
        .map(|v| if v.is_nan() { 0.0 } else { v })
        .collect();
    level(&gated)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flat() -> Vec<Stage> {
        vec![Stage {
            b: vec![1.0],
            a: vec![1.0],
            gain: 1.0,
        }]
    }

    #[test]
    fn a_full_scale_square_wave_reads_its_mean_square() {
        let ch = vec![1.0_f32; 48000 * 2];
        let got = integrated(&[ch], 48000.0, &flat(), 0.4, 0.75);
        assert!((got - -0.691).abs() < 1e-9);
    }

    #[test]
    fn silence_is_minus_infinity() {
        let ch = vec![0.0_f32; 48000];
        assert_eq!(
            integrated(&[ch], 48000.0, &flat(), 0.4, 0.75),
            f64::NEG_INFINITY
        );
    }
}
