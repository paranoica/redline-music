// Times the parts of the master's clip on three minutes of noise: cargo run --release --example timing
use std::time::Instant;

use redline_dsp_core::filters;

fn main() {
    let n = 48_000 * 187;
    let x: Vec<f32> = (0..n)
        .map(|i| (((i as f64 * 12.9898).sin() * 43758.5453).fract() * 0.6 - 0.3) as f32)
        .collect();
    let h: Vec<f32> = (0..161)
        .map(|i| (((i as f64 - 80.0) * 0.05).cos() / 8.0) as f32)
        .collect();
    let t = Instant::now();
    let up = filters::upfirdn(&h, &x, 8, 1, 10, n * 8);
    println!("up {:.2}", t.elapsed().as_secs_f64());
    let sos = [[0.1, 0.2, 0.1, 1.0, -0.5, 0.2]; 4];
    let zi = [[0.0, 0.0]; 4];
    let t = Instant::now();
    let f = filters::sosfiltfilt(&sos, &zi, &up, 27);
    println!("sosfiltfilt {:.2}", t.elapsed().as_secs_f64());
    let t = Instant::now();
    let d = filters::upfirdn(&h, &f, 1, 8, 10, n);
    println!("down {:.2} {}", t.elapsed().as_secs_f64(), d[n / 2]);
    let t = Instant::now();
    let l = filters::lfilter(&[1.0, -1.9, 0.9], &[1.0, -1.98, 0.98], &x);
    println!("lfilter {:.3} {}", t.elapsed().as_secs_f64(), l[n / 2]);
}
