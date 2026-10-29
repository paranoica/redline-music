//! Polyphase resampling and IIR filtering, as scipy computes them.

/// Run f with numbers too small for normal floats read and written as zero.
///
/// A filter falling silent leaves its state decaying through subnormal numbers, which the
/// processor handles many times slower; a vocal stem is half silence. What changes is below
/// 1e-308, far under anything audible or measured.
pub fn flushing_tiny<R>(f: impl FnOnce() -> R) -> R {
    #[cfg(target_arch = "x86_64")]
    {
        #[allow(deprecated)]
        use std::arch::x86_64::{_mm_getcsr, _mm_setcsr};
        // flush-to-zero (bit 15) and denormals-are-zero (bit 6)
        #[allow(deprecated)]
        let before = unsafe { _mm_getcsr() };
        #[allow(deprecated)]
        unsafe {
            _mm_setcsr(before | 0x8040)
        };
        let out = f();
        #[allow(deprecated)]
        unsafe {
            _mm_setcsr(before)
        };
        out
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        f()
    }
}

/// `upfirdn(h, x, up, down)[skip..skip + n_out]`: upsample by zero stuffing, filter, keep
/// every `down`-th sample, in single precision as scipy does for float32. Only the kept
/// samples are computed, each a dot product of one phase of the taps with a run of x.
pub fn upfirdn(
    h: &[f32],
    x: &[f32],
    up: usize,
    down: usize,
    skip: usize,
    n_out: usize,
) -> Vec<f32> {
    // phase p holds h[p], h[p + up], ... reversed, padded to one length with leading zeros
    let len = h.len().div_ceil(up);
    let phases: Vec<Vec<f32>> = (0..up)
        .map(|p| {
            let mut taps: Vec<f32> = h.iter().skip(p).step_by(up).copied().collect();
            taps.resize(len, 0.0);
            taps.reverse();
            taps
        })
        .collect();
    // x with len - 1 zeros before it and len after, so every run is a plain slice
    let mut padded = vec![0.0; len - 1];
    padded.extend_from_slice(x);
    padded.resize(padded.len() + len, 0.0);
    let mut out = Vec::with_capacity(n_out);
    let (mut phase, mut newest) = (((skip * down) % up), (skip * down) / up);
    let (step_phase, step_newest) = (down % up, down / up);
    for _ in 0..n_out {
        // the run ending at x[newest] starts at padded[newest]
        out.push(if newest < x.len() + len - 1 {
            dot(&phases[phase], &padded[newest..newest + len])
        } else {
            0.0
        });
        phase += step_phase;
        newest += step_newest;
        if phase >= up {
            phase -= up;
            newest += 1;
        }
    }
    out
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    // eight running sums the compiler keeps in one vector register
    let mut acc = [0.0f32; 8];
    let (ca, cb) = (a.chunks_exact(8), b.chunks_exact(8));
    let tail: f32 = ca
        .remainder()
        .iter()
        .zip(cb.remainder())
        .map(|(p, q)| p * q)
        .sum();
    for (pa, pb) in ca.zip(cb) {
        for l in 0..8 {
            acc[l] += pa[l] * pb[l];
        }
    }
    acc.iter().sum::<f32>() + tail
}

#[inline(always)]
fn section(c: &[f64; 6], z: &mut [f64; 2], input: f64) -> f64 {
    let y = c[0] * input + z[0];
    z[0] = c[1] * input - c[4] * y + z[1];
    z[1] = c[2] * input - c[5] * y;
    y
}

/// The sample a step reaches: from the front, or from the back when filtering backwards.
#[inline(always)]
fn at<const BACK: bool>(n: usize, m: usize) -> usize {
    if BACK {
        n - 1 - m
    } else {
        m
    }
}

/// One step at the start or the end, where some sections have no sample yet or any more.
fn edge_step<const BACK: bool>(
    sos: &[[f64; 6]],
    state: &mut [[f64; 2]],
    carry: &mut [f64],
    step: usize,
    x: &mut [f64],
) {
    let (n, sections) = (x.len(), sos.len());
    for k in (0..sections).rev() {
        let Some(m) = step.checked_sub(k).filter(|&m| m < n) else {
            continue;
        };
        let i = at::<BACK>(n, m);
        let input = if k == 0 { x[i] } else { carry[k - 1] };
        let y = section(&sos[k], &mut state[k], input);
        if k + 1 == sections {
            x[i] = y;
        } else {
            carry[k] = y;
        }
    }
}

/// Cascaded second-order sections, direct form II transposed, from a starting state; with
/// BACK the samples are taken from the last to the first.
///
/// Section k works on sample n - k while section 0 takes sample n: the sections are
/// independent within a step, so they run side by side instead of one after another. Each
/// section does the same arithmetic on the same samples as a plain loop would.
pub fn sosfilt<const BACK: bool>(sos: &[[f64; 6]], x: &mut [f64], zi: &[[f64; 2]], scale: f64) {
    let (n, sections) = (x.len(), sos.len());
    if sections == 0 || n == 0 {
        return;
    }
    let mut state: Vec<[f64; 2]> = zi.iter().map(|z| [z[0] * scale, z[1] * scale]).collect();
    // carry[k]: what section k gave on its last step, waiting for section k + 1
    let mut carry = vec![0.0; sections];
    let last = sections - 1;
    let steady = last..n.max(last);
    for step in 0..steady.start.min(n + last) {
        edge_step::<BACK>(sos, &mut state, &mut carry, step, x);
    }
    // every section has a sample in this stretch: no bounds to check
    for step in steady.clone() {
        for k in (1..sections).rev() {
            let y = section(&sos[k], &mut state[k], carry[k - 1]);
            if k == last {
                x[at::<BACK>(n, step - last)] = y;
            } else {
                carry[k] = y;
            }
        }
        let i = at::<BACK>(n, step);
        let y = section(&sos[0], &mut state[0], x[i]);
        if last == 0 {
            x[i] = y;
        } else {
            carry[0] = y;
        }
    }
    for step in steady.end.max(last)..n + last {
        edge_step::<BACK>(sos, &mut state, &mut carry, step, x);
    }
}

/// Zero-phase filtering with odd extension at both ends, as `scipy.signal.sosfiltfilt`,
/// in double precision on single-precision samples.
pub fn sosfiltfilt(sos: &[[f64; 6]], zi: &[[f64; 2]], x: &[f32], edge: usize) -> Vec<f32> {
    let n = x.len();
    let (first, last) = (x[0] as f64, x[n - 1] as f64);
    let mut ext = Vec::with_capacity(n + 2 * edge);
    ext.extend((1..=edge).rev().map(|i| 2.0 * first - x[i] as f64));
    ext.extend(x.iter().map(|&v| v as f64));
    ext.extend((1..=edge).map(|i| 2.0 * last - x[n - 1 - i] as f64));
    let start = ext[0];
    sosfilt::<false>(sos, &mut ext, zi, start);
    let end = ext[ext.len() - 1];
    sosfilt::<true>(sos, &mut ext, zi, end);
    ext[edge..edge + n].iter().map(|&v| v as f32).collect()
}

/// `scipy.signal.lfilter(b, a, x)` from rest for a filter of order two or less, direct form
/// II transposed.
pub fn lfilter(b: &[f64], a: &[f64], x: &[f32]) -> Vec<f64> {
    let norm = a[0];
    let c = |v: &[f64], i: usize| v.get(i).copied().unwrap_or(0.0) / norm;
    let (b0, b1, b2) = (c(b, 0), c(b, 1), c(b, 2));
    let (a1, a2) = (c(a, 1), c(a, 2));
    let (mut z0, mut z1) = (0.0, 0.0);
    x.iter()
        .map(|&v| {
            let v = v as f64;
            let y = b0 * v + z0;
            z0 = b1 * v - a1 * y + z1;
            z1 = b2 * v - a2 * y;
            y
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn upfirdn_by_one_is_a_convolution() {
        let y = upfirdn(&[1.0, 2.0], &[1.0, 1.0, 1.0], 1, 1, 0, 4);
        assert_eq!(y, vec![1.0, 3.0, 3.0, 2.0]);
    }

    #[test]
    fn upfirdn_stuffs_zeros_and_keeps_every_down_th() {
        // x upsampled by 2 is [1, 0, 2, 0]; with h = [1] and down 2 that is [1, 2]
        assert_eq!(upfirdn(&[1.0], &[1.0, 2.0], 2, 2, 0, 2), vec![1.0, 2.0]);
    }

    #[test]
    fn side_by_side_sections_give_the_plain_cascade_bit_for_bit() {
        let sos = [
            [0.2, 0.3, 0.1, 1.0, -0.4, 0.1],
            [0.5, -0.2, 0.3, 1.0, 0.3, 0.2],
            [1.0, 0.1, 0.0, 1.0, -0.1, 0.05],
        ];
        let zi = [[0.1, 0.2], [0.0, -0.1], [0.3, 0.0]];
        let x: Vec<f64> = (0..200).map(|i| (i as f64 * 0.3).sin()).collect();
        let mut got = x.clone();
        sosfilt::<false>(&sos, &mut got, &zi, 0.7);
        // fewer samples than sections: only the edge steps run
        let mut short = x[..2].to_vec();
        sosfilt::<false>(&sos, &mut short, &zi, 0.7);
        assert_eq!(short[0].to_bits(), got[0].to_bits());
        assert_eq!(short[1].to_bits(), got[1].to_bits());
        let mut state: Vec<[f64; 2]> = zi.iter().map(|z| [z[0] * 0.7, z[1] * 0.7]).collect();
        for (i, &v) in x.iter().enumerate() {
            let mut s = v;
            for (c, z) in sos.iter().zip(state.iter_mut()) {
                let y = c[0] * s + z[0];
                z[0] = c[1] * s - c[4] * y + z[1];
                z[1] = c[2] * s - c[5] * y;
                s = y;
            }
            assert_eq!(got[i].to_bits(), s.to_bits());
        }
    }

    #[test]
    fn upfirdn_matches_the_plain_sum_at_every_phase_and_edge() {
        let h: Vec<f32> = (0..23).map(|i| (i as f32 * 0.37).sin()).collect();
        let x: Vec<f32> = (0..50).map(|i| (i as f32 * 0.11).cos()).collect();
        for (up, down, skip) in [(8, 1, 0), (1, 8, 0), (3, 2, 0), (8, 1, 5), (1, 8, 3)] {
            let n_out = (x.len() * up + h.len()) / down - skip;
            let got = upfirdn(&h, &x, up, down, skip, n_out);
            for (k, g) in got.iter().enumerate() {
                let i = (k + skip) * down;
                let want: f64 = (0..h.len())
                    .filter(|&j| j <= i && (i - j) % up == 0 && (i - j) / up < x.len())
                    .map(|j| h[j] as f64 * x[(i - j) / up] as f64)
                    .sum();
                assert!(
                    (*g as f64 - want).abs() < 1e-5,
                    "up {up} down {down} skip {skip} k {k}"
                );
            }
        }
    }

    #[test]
    fn lfilter_of_a_one_pole_decays() {
        let y = lfilter(&[1.0], &[1.0, -0.5], &[1.0, 0.0, 0.0]);
        assert_eq!(y, vec![1.0, 0.5, 0.25]);
    }

    #[test]
    fn a_pass_through_section_changes_nothing() {
        let x = [0.1f32, -0.2, 0.3, 0.4, -0.5, 0.6, 0.7];
        let y = sosfiltfilt(&[[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]], &[[0.0, 0.0]], &x, 3);
        assert_eq!(y, x.to_vec());
    }

    #[test]
    fn filtering_backwards_is_filtering_the_reversed_samples() {
        let sos = [
            [0.2, 0.3, 0.1, 1.0, -0.4, 0.1],
            [0.5, -0.2, 0.3, 1.0, 0.3, 0.2],
        ];
        let zi = [[0.1, 0.2], [0.0, -0.1]];
        let x: Vec<f64> = (0..100).map(|i| (i as f64 * 0.7).cos()).collect();
        let mut back = x.clone();
        sosfilt::<true>(&sos, &mut back, &zi, 1.3);
        let mut reversed: Vec<f64> = x.iter().rev().copied().collect();
        sosfilt::<false>(&sos, &mut reversed, &zi, 1.3);
        reversed.reverse();
        assert_eq!(back, reversed);
    }
}
