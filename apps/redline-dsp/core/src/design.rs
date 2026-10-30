//! Filter design: RBJ cookbook biquads and Butterworth sections, as scipy designs them.
//!
//! A section is `[b0, b1, b2, 1, a1, a2]`, scipy's `sos` row with a0 divided out.

use std::f64::consts::PI;

pub type Sos = [f64; 6];

fn normalised(b: [f64; 3], a: [f64; 3]) -> Sos {
    [
        b[0] / a[0],
        b[1] / a[0],
        b[2] / a[0],
        1.0,
        a[1] / a[0],
        a[2] / a[0],
    ]
}

/// The cookbook's cos(w0) and alpha.
fn corner(freq: f64, q: f64, rate: f64) -> (f64, f64) {
    let w0 = 2.0 * PI * freq / rate;
    (w0.cos(), w0.sin() / (2.0 * q))
}

pub fn peaking(freq: f64, gain_db: f64, q: f64, rate: f64) -> Sos {
    let amp = 10f64.powf(gain_db / 40.0);
    let (cos, alpha) = corner(freq, q, rate);
    normalised(
        [1.0 + alpha * amp, -2.0 * cos, 1.0 - alpha * amp],
        [1.0 + alpha / amp, -2.0 * cos, 1.0 - alpha / amp],
    )
}

/// Low shelf; q of 1/sqrt(2) is the cookbook's slope of one.
pub fn low_shelf(freq: f64, gain_db: f64, q: f64, rate: f64) -> Sos {
    let amp = 10f64.powf(gain_db / 40.0);
    let (cos, alpha) = corner(freq, q, rate);
    let root = 2.0 * amp.sqrt() * alpha;
    normalised(
        [
            amp * ((amp + 1.0) - (amp - 1.0) * cos + root),
            2.0 * amp * ((amp - 1.0) - (amp + 1.0) * cos),
            amp * ((amp + 1.0) - (amp - 1.0) * cos - root),
        ],
        [
            (amp + 1.0) + (amp - 1.0) * cos + root,
            -2.0 * ((amp - 1.0) + (amp + 1.0) * cos),
            (amp + 1.0) + (amp - 1.0) * cos - root,
        ],
    )
}

/// High shelf; q of 1/sqrt(2) is the cookbook's slope of one.
pub fn high_shelf(freq: f64, gain_db: f64, q: f64, rate: f64) -> Sos {
    let amp = 10f64.powf(gain_db / 40.0);
    let (cos, alpha) = corner(freq, q, rate);
    let root = 2.0 * amp.sqrt() * alpha;
    normalised(
        [
            amp * ((amp + 1.0) + (amp - 1.0) * cos + root),
            -2.0 * amp * ((amp - 1.0) + (amp + 1.0) * cos),
            amp * ((amp + 1.0) + (amp - 1.0) * cos - root),
        ],
        [
            (amp + 1.0) - (amp - 1.0) * cos + root,
            2.0 * ((amp - 1.0) - (amp + 1.0) * cos),
            (amp + 1.0) - (amp - 1.0) * cos - root,
        ],
    )
}

pub fn lowpass(freq: f64, q: f64, rate: f64) -> Sos {
    let (cos, alpha) = corner(freq, q, rate);
    normalised(
        [(1.0 - cos) / 2.0, 1.0 - cos, (1.0 - cos) / 2.0],
        [1.0 + alpha, -2.0 * cos, 1.0 - alpha],
    )
}

pub fn highpass(freq: f64, q: f64, rate: f64) -> Sos {
    let (cos, alpha) = corner(freq, q, rate);
    normalised(
        [(1.0 + cos) / 2.0, -(1.0 + cos), (1.0 + cos) / 2.0],
        [1.0 + alpha, -2.0 * cos, 1.0 - alpha],
    )
}

/// Band pass with a peak of 0 dB.
pub fn bandpass(freq: f64, q: f64, rate: f64) -> Sos {
    let (cos, alpha) = corner(freq, q, rate);
    normalised([alpha, 0.0, -alpha], [1.0 + alpha, -2.0 * cos, 1.0 - alpha])
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Band {
    Lowpass(f64),
    Highpass(f64),
    Bandpass(f64, f64),
}

#[derive(Clone, Copy, Debug, PartialEq)]
struct C {
    re: f64,
    im: f64,
}

impl C {
    const ZERO: C = C { re: 0.0, im: 0.0 };

    fn real(re: f64) -> C {
        C { re, im: 0.0 }
    }

    fn add(self, o: C) -> C {
        C {
            re: self.re + o.re,
            im: self.im + o.im,
        }
    }

    fn sub(self, o: C) -> C {
        C {
            re: self.re - o.re,
            im: self.im - o.im,
        }
    }

    fn mul(self, o: C) -> C {
        C {
            re: self.re * o.re - self.im * o.im,
            im: self.re * o.im + self.im * o.re,
        }
    }

    fn div(self, o: C) -> C {
        let d = o.re * o.re + o.im * o.im;
        C {
            re: (self.re * o.re + self.im * o.im) / d,
            im: (self.im * o.re - self.re * o.im) / d,
        }
    }

    fn scale(self, k: f64) -> C {
        C {
            re: self.re * k,
            im: self.im * k,
        }
    }

    fn abs(self) -> f64 {
        self.re.hypot(self.im)
    }

    fn conj(self) -> C {
        C {
            re: self.re,
            im: -self.im,
        }
    }

    fn sqrt(self) -> C {
        let r = self.abs();
        let re = ((r + self.re) / 2.0).sqrt();
        let im = ((r - self.re) / 2.0).sqrt();
        C {
            re,
            im: if self.im < 0.0 { -im } else { im },
        }
    }

    fn is_real(self) -> bool {
        self.im == 0.0
    }
}

fn product(v: &[C]) -> C {
    v.iter().fold(C::real(1.0), |acc, &x| acc.mul(x))
}

/// `scipy.signal.butter(order, band, fs=rate, output="sos")`: the analog prototype moved to
/// the band, the bilinear transform, and scipy's own pairing of poles and zeros.
pub fn butterworth(order: usize, band: Band, rate: f64) -> Vec<Sos> {
    // scipy normalises to fs = 2 and warps there; doing the same keeps the rounding alike
    let fs = 2.0;
    let warp = |f: f64| 2.0 * fs * (PI * (2.0 * f / rate) / fs).tan();
    let n = order as f64;
    let proto: Vec<C> = (0..order)
        .map(|i| {
            let m = -n + 1.0 + 2.0 * i as f64;
            let angle = PI * m / (2.0 * n);
            C {
                re: -angle.cos(),
                im: -angle.sin(),
            }
        })
        .collect();
    let (z, p, k) = match band {
        Band::Lowpass(f) => {
            let wo = warp(f);
            let p: Vec<C> = proto.iter().map(|p| p.scale(wo)).collect();
            (vec![], p, wo.powi(order as i32))
        }
        Band::Highpass(f) => {
            let wo = warp(f);
            let p: Vec<C> = proto.iter().map(|p| C::real(wo).div(*p)).collect();
            let minus: Vec<C> = proto.iter().map(|p| p.scale(-1.0)).collect();
            let k = C::real(1.0).div(product(&minus)).re;
            (vec![C::ZERO; order], p, k)
        }
        Band::Bandpass(low, high) => {
            let (w1, w2) = (warp(low), warp(high));
            let (bw, wo) = (w2 - w1, (w1 * w2).sqrt());
            let half: Vec<C> = proto.iter().map(|p| p.scale(bw / 2.0)).collect();
            let root = |p: &C| p.mul(*p).sub(C::real(wo * wo)).sqrt();
            let mut p: Vec<C> = half.iter().map(|p| p.add(root(p))).collect();
            p.extend(half.iter().map(|p| p.sub(root(p))));
            (vec![C::ZERO; order], p, bw.powi(order as i32))
        }
    };
    let fs2 = C::real(2.0 * fs);
    let bilinear = |v: &C| fs2.add(*v).div(fs2.sub(*v));
    let mut zd: Vec<C> = z.iter().map(bilinear).collect();
    zd.resize(p.len(), C::real(-1.0));
    let pd: Vec<C> = p.iter().map(bilinear).collect();
    let num: Vec<C> = z.iter().map(|z| fs2.sub(*z)).collect();
    let den: Vec<C> = p.iter().map(|p| fs2.sub(*p)).collect();
    let k = k * product(&num).div(product(&den)).re;
    zpk2sos(zd, pd, k)
}

/// One of each conjugate pair, with positive imaginary part, sorted by real part; then the
/// real values, sorted. Values within scipy's tolerance of the real axis are made real.
fn cplxreal(v: Vec<C>) -> Vec<C> {
    let tol = 100.0 * f64::EPSILON;
    let (mut real, mut complex): (Vec<C>, Vec<C>) = (vec![], vec![]);
    for x in v {
        if x.im.abs() <= tol * x.abs() {
            real.push(C::real(x.re));
        } else if x.im > 0.0 {
            complex.push(x);
        }
    }
    complex.sort_by(|a, b| a.re.total_cmp(&b.re).then(a.im.total_cmp(&b.im)));
    real.sort_by(|a, b| a.re.total_cmp(&b.re));
    complex.extend(real);
    complex
}

#[derive(PartialEq)]
enum Kind {
    Any,
    Real,
    Complex,
}

fn nearest(from: &[C], to: C, kind: Kind) -> usize {
    let mut order: Vec<usize> = (0..from.len()).collect();
    order.sort_by(|&a, &b| from[a].sub(to).abs().total_cmp(&from[b].sub(to).abs()));
    order
        .into_iter()
        .find(|&i| match kind {
            Kind::Any => true,
            Kind::Real => from[i].is_real(),
            Kind::Complex => !from[i].is_real(),
        })
        .expect("a zero of the kind asked for")
}

/// The one closest to the unit circle.
fn worst(v: &[C]) -> usize {
    (0..v.len())
        .min_by(|&a, &b| {
            (1.0 - v[a].abs())
                .abs()
                .total_cmp(&(1.0 - v[b].abs()).abs())
        })
        .expect("a pole left")
}

fn poly(roots: &[C]) -> Vec<f64> {
    let mut c = vec![C::real(1.0)];
    for r in roots {
        let mut next = c.clone();
        next.push(C::ZERO);
        for (i, v) in c.iter().enumerate() {
            next[i + 1] = next[i + 1].sub(v.mul(*r));
        }
        c = next;
    }
    c.into_iter().map(|v| v.re).collect()
}

fn single(z: &[C], p: &[C]) -> Sos {
    let (b, a) = (poly(z), poly(p));
    let mut s = [0.0; 6];
    s[3 - b.len()..3].copy_from_slice(&b);
    s[6 - a.len()..6].copy_from_slice(&a);
    s
}

/// `scipy.signal.zpk2sos` with its default pairing for digital filters, "nearest".
fn zpk2sos(mut z: Vec<C>, mut p: Vec<C>, k: f64) -> Vec<Sos> {
    let sections = p.len().max(z.len()).div_ceil(2);
    if p.len() % 2 == 1 {
        p.push(C::ZERO);
        z.push(C::ZERO);
    }
    let (mut z, mut p) = (cplxreal(z), cplxreal(p));
    let reals = |v: &[C]| v.iter().filter(|x| x.is_real()).count();
    let mut sos = vec![[0.0; 6]; sections];
    // the poles closest to the unit circle go last
    for si in (0..sections).rev() {
        let p1 = p.remove(worst(&p));
        if p1.is_real() && reals(&p) == 0 {
            let z1 = z.remove(nearest(&z, p1, Kind::Real));
            sos[si] = single(&[z1, C::ZERO], &[p1, C::ZERO]);
        } else if p.len() + 1 == z.len() && !p1.is_real() && reals(&p) == 1 && reals(&z) == 1 {
            let z1 = z.remove(nearest(&z, p1, Kind::Complex));
            sos[si] = single(&[z1, z1.conj()], &[p1, p1.conj()]);
        } else {
            let p2 = if p1.is_real() {
                let real: Vec<usize> = (0..p.len()).filter(|&i| p[i].is_real()).collect();
                let pick: Vec<C> = real.iter().map(|&i| p[i]).collect();
                p.remove(real[worst(&pick)])
            } else {
                p1.conj()
            };
            sos[si] = if z.is_empty() {
                single(&[], &[p1, p2])
            } else {
                let z1 = z.remove(nearest(&z, p1, Kind::Any));
                if !z1.is_real() {
                    single(&[z1, z1.conj()], &[p1, p2])
                } else if !z.is_empty() {
                    let z2 = z.remove(nearest(&z, p1, Kind::Real));
                    single(&[z1, z2], &[p1, p2])
                } else {
                    single(&[z1], &[p1, p2])
                }
            };
        }
    }
    for c in &mut sos[0][..3] {
        *c *= k;
    }
    sos
}
