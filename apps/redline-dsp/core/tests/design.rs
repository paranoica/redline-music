//! The expected coefficients were computed once with scipy 1.18.1 (`scipy.signal.butter`) and
//! with redline's Python RBJ path (`redline.core.dsp`), and pasted here as literals.

use redline_dsp_core::design::{self, Band, Sos};
use std::f64::consts::{FRAC_1_SQRT_2, PI};

fn close(got: &[Sos], want: &[Sos]) {
    assert_eq!(got.len(), want.len(), "number of sections");
    for (k, (g, w)) in got.iter().zip(want).enumerate() {
        for i in 0..6 {
            assert!(
                (g[i] - w[i]).abs() <= 1e-10 * w[i].abs() + 1e-15,
                "section {k} coefficient {i}: got {} want {}",
                g[i],
                w[i]
            );
        }
    }
}

fn magnitude(s: &Sos, freq: f64, rate: f64) -> f64 {
    let w = 2.0 * PI * freq / rate;
    let at = |c0: f64, c1: f64, c2: f64| {
        let re = c0 + c1 * w.cos() + c2 * (2.0 * w).cos();
        let im = -c1 * w.sin() - c2 * (2.0 * w).sin();
        re.hypot(im)
    };
    at(s[0], s[1], s[2]) / at(s[3], s[4], s[5])
}

#[test]
fn peaking_matches_the_python_rbj_path() {
    close(
        &[design::peaking(1000.0, 6.0, 1.0, 48000.0)],
        &[[
            1.043953086990335,
            -1.8953207239365961,
            0.8677222847598566,
            1.0,
            -1.8953207239365961,
            0.9116753717501915,
        ]],
    );
    close(
        &[design::peaking(8000.0, -4.5, 2.5, 44100.0)],
        &[[
            0.9229446250810012,
            -0.6763096613491243,
            0.6959118649269909,
            1.0,
            -0.6763096613491243,
            0.618856490007992,
        ]],
    );
}

#[test]
fn a_peak_of_no_gain_passes_everything() {
    let s = design::peaking(1000.0, 0.0, 1.0, 48000.0);
    assert_eq!([s[0], s[1], s[2]], [s[3], s[4], s[5]]);
}

#[test]
fn shelves_at_q_one_over_root_two_are_the_python_slope_one_shelves() {
    close(
        &[design::low_shelf(120.0, 3.0, FRAC_1_SQRT_2, 44100.0)],
        &[[
            1.0020924587298137,
            -1.9777718605831731,
            0.9760229994595985,
            1.0,
            -1.9778220351478621,
            0.9780652836247238,
        ]],
    );
    close(
        &[design::high_shelf(8000.0, -4.0, FRAC_1_SQRT_2, 44100.0)],
        &[[
            0.7492094076827812,
            -0.28562791782520663,
            0.1478819085994033,
            1.0,
            -0.6315101321363241,
            0.2429735305933019,
        ]],
    );
}

#[test]
fn rbj_low_and_high_pass_at_q_one_over_root_two_are_scipys_second_order_butterworth() {
    close(
        &[design::lowpass(1000.0, FRAC_1_SQRT_2, 48000.0)],
        &[[
            0.003916126660547369,
            0.007832253321094738,
            0.003916126660547369,
            1.0,
            -1.815341082704568,
            0.8310055893467575,
        ]],
    );
    close(
        &[design::highpass(150.0, FRAC_1_SQRT_2, 44100.0)],
        &[[
            0.9850017578724192,
            -1.9700035157448383,
            0.9850017578724192,
            1.0,
            -1.96977855582618,
            0.9702284756634976,
        ]],
    );
}

#[test]
fn resonant_low_pass_and_band_pass_follow_the_cookbook() {
    // the cookbook's formulas evaluated in Python: there is no scipy designer for them
    let lp = design::lowpass(2000.0, 4.0, 44100.0);
    close(
        &[lp],
        &[[
            0.019477952710758754,
            0.03895590542151751,
            0.019477952710758754,
            1.0,
            -1.8541960646619569,
            0.9321078755049916,
        ]],
    );
    let bp = design::bandpass(1000.0, 2.0, 48000.0);
    close(
        &[bp],
        &[[
            0.031600378776413744,
            0.0,
            -0.031600378776413744,
            1.0,
            -1.920229656436938,
            0.9367992424471726,
        ]],
    );
    // the cookbook's own promises: a low pass peaks at Q at its corner, a band pass at 0 dB
    assert!((magnitude(&lp, 2000.0, 44100.0) - 4.0).abs() < 1e-9);
    assert!((magnitude(&bp, 1000.0, 48000.0) - 1.0).abs() < 1e-9);
}

#[test]
fn butterworth_low_pass_matches_scipy_section_for_section() {
    close(
        &design::butterworth(8, Band::Lowpass(20000.0), 352800.0),
        &[
            [
                4.395537201578124e-07,
                8.791074403156248e-07,
                4.395537201578124e-07,
                1.0,
                -1.3967639534771323,
                0.49030707576363375,
            ],
            [1.0, 2.0, 1.0, 1.0, -1.4531430916671655, 0.5504619991212243],
            [1.0, 2.0, 1.0, 1.0, -1.5702578798940245, 0.6754201190215495],
            [1.0, 2.0, 1.0, 1.0, -1.7550689028168127, 0.8726081796493295],
        ],
    );
    // odd orders: scipy pads a pole and a zero at the origin, and where they land differs
    close(
        &design::butterworth(3, Band::Lowpass(1000.0), 48000.0),
        &[
            [
                0.00024700081539115486,
                0.0004940016307823097,
                0.00024700081539115486,
                1.0,
                -0.8769764629927568,
                0.0,
            ],
            [1.0, 1.0, 0.0, 1.0, -1.861408444532108, 0.877470464623539],
        ],
    );
    close(
        &design::butterworth(5, Band::Lowpass(18000.0), 44100.0),
        &[
            [
                0.38670036277923986,
                0.38670036277923986,
                0.0,
                1.0,
                0.5422641081967179,
                0.0,
            ],
            [1.0, 2.0, 1.0, 1.0, 1.1629234339025964, 0.3875909073458065],
            [1.0, 2.0, 1.0, 1.0, 1.4343706441692239, 0.7114795399158379],
        ],
    );
}

#[test]
fn butterworth_high_pass_matches_scipy_section_for_section() {
    close(
        &design::butterworth(4, Band::Highpass(80.0), 48000.0),
        &[
            [
                0.9864108095372399,
                -1.9728216190744798,
                0.9864108095372399,
                1.0,
                -1.9807274601092266,
                0.9808360706077945,
            ],
            [1.0, -2.0, 1.0, 1.0, -1.991908009819602, 0.9920172333884194],
        ],
    );
    close(
        &design::butterworth(1, Band::Highpass(500.0), 44100.0),
        &[[
            0.9655920584451844,
            -0.9655920584451844,
            0.0,
            1.0,
            -0.9311841168903688,
            0.0,
        ]],
    );
}

#[test]
fn butterworth_band_pass_matches_scipy_section_for_section() {
    close(
        &design::butterworth(2, Band::Bandpass(300.0, 3400.0), 8000.0),
        &[
            [
                0.6031972438993125,
                1.206394487798625,
                0.6031972438993125,
                1.0,
                1.3423045143340906,
                0.516380128598303,
            ],
            [1.0, -2.0, 1.0, 1.0, -1.6675616713630557, 0.7176625588018256],
        ],
    );
    close(
        &design::butterworth(3, Band::Bandpass(100.0, 1000.0), 48000.0),
        &[
            [
                0.0001822920661029149,
                0.0003645841322058298,
                0.0001822920661029149,
                1.0,
                -1.8841485981310635,
                0.8992343105502757,
            ],
            [1.0, 0.0, -1.0, 1.0, -1.8870024308748783, 0.8886221250527707],
            [1.0, -2.0, 1.0, 1.0, -1.9884205281254002, 0.9886040809997247],
        ],
    );
}
