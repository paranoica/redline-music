//! The heavy inner loops of redline's master and the effects rack, called from Python.
//!
//! The master's filters are designed in Python by scipy and pyloudnorm and handed over, so the
//! two paths cannot drift apart in their coefficients; only the loops live in the core. Each
//! channel gets its own thread here, as the core has none.

use redline_dsp_core::effect::{effects_json, Rack, RackError};
use redline_dsp_core::{filters, loudness};

use numpy::{PyArray1, PyArray2, PyReadonlyArray1, PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn columns(x: &PyReadonlyArray2<f32>) -> Vec<Vec<f32>> {
    let view = x.as_array();
    view.columns().into_iter().map(|c| c.to_vec()).collect()
}

fn to_array<'py>(py: Python<'py>, channels: &[Vec<f32>]) -> PyResult<Bound<'py, PyArray2<f32>>> {
    let rows = channels.first().map_or(0, Vec::len);
    let flat: Vec<f32> = (0..rows)
        .flat_map(|r| channels.iter().map(move |c| c[r]))
        .collect();
    let arr = numpy::ndarray::Array2::from_shape_vec((rows, channels.len()), flat)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyArray2::from_owned_array(py, arr))
}

fn sections(sos: Vec<Vec<f64>>) -> PyResult<Vec<[f64; 6]>> {
    sos.into_iter()
        .map(|s| {
            s.try_into()
                .map_err(|_| PyValueError::new_err("a section has six coefficients"))
        })
        .collect()
}

fn states(zi: Vec<Vec<f64>>) -> PyResult<Vec<[f64; 2]>> {
    zi.into_iter()
        .map(|s| {
            s.try_into()
                .map_err(|_| PyValueError::new_err("a section's state has two values"))
        })
        .collect()
}

/// A resampling plan: padded taps, the up and down factors, samples to skip, samples out.
type Plan = (Vec<f32>, usize, usize, usize, usize);

fn checked(plan: &Plan) -> PyResult<()> {
    if plan.0.is_empty() || plan.1 == 0 || plan.2 == 0 {
        return Err(PyValueError::new_err(
            "a plan needs taps and factors of one or more",
        ));
    }
    Ok(())
}

fn clip(x: &mut [f32], ceiling: f32) {
    for v in x.iter_mut() {
        *v = v.clamp(-ceiling, ceiling);
    }
}

/// Gain, then clip and band-limit at the raised rate `passes` times, clip, and come back down.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn oversampled_clip<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray2<'py, f32>,
    gain: f32,
    ceiling: f32,
    up: Plan,
    down: Plan,
    sos: Vec<Vec<f64>>,
    zi: Vec<Vec<f64>>,
    edge: usize,
    passes: usize,
) -> PyResult<Bound<'py, PyArray2<f32>>> {
    let sos = sections(sos)?;
    let zi = states(zi)?;
    checked(&up)?;
    checked(&down)?;
    let channels = columns(&samples);
    if channels.is_empty() || channels.iter().any(|c| c.len() * up.1 <= edge) {
        return Err(PyValueError::new_err(
            "too short to filter at the raised rate",
        ));
    }
    let out = py.detach(|| {
        per_channel(&channels, |ch| {
            filters::flushing_tiny(|| {
                let gained: Vec<f32> = ch.iter().map(|&v| v * gain).collect();
                let mut v = filters::upfirdn(&up.0, &gained, up.1, up.2, up.3, up.4);
                for _ in 0..passes {
                    clip(&mut v, ceiling);
                    v = filters::sosfiltfilt(&sos, &zi, &v, edge);
                }
                clip(&mut v, ceiling);
                let mut back = filters::upfirdn(&down.0, &v, down.1, down.2, down.3, down.4);
                back.truncate(ch.len());
                back
            })
        })
    });
    to_array(py, &out)
}

/// One channel resampled by a plan from `scipy.signal.resample_poly`'s own filter.
#[pyfunction]
fn resample<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray1<'py, f32>,
    plan: Plan,
) -> PyResult<Bound<'py, PyArray1<f32>>> {
    checked(&plan)?;
    let x = samples.as_array().to_vec();
    let out = py.detach(|| {
        filters::flushing_tiny(|| filters::upfirdn(&plan.0, &x, plan.1, plan.2, plan.3, plan.4))
    });
    Ok(PyArray1::from_vec(py, out))
}

/// Every channel through cascaded sections from rest, in double precision, as `sosfilt`.
#[pyfunction]
fn sosfilt<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray2<'py, f32>,
    sos: Vec<Vec<f64>>,
) -> PyResult<Bound<'py, PyArray2<f32>>> {
    let sos = sections(sos)?;
    let channels = columns(&samples);
    let rest = vec![[0.0, 0.0]; sos.len()];
    let out = py.detach(|| {
        per_channel(&channels, |ch| {
            filters::flushing_tiny(|| {
                let mut wide: Vec<f64> = ch.iter().map(|&v| v as f64).collect();
                filters::sosfilt::<false>(&sos, &mut wide, &rest, 0.0);
                wide.into_iter().map(|v| v as f32).collect::<Vec<f32>>()
            })
        })
    });
    to_array(py, &out)
}

/// Integrated loudness of samples (rows, channels) through the given K-weighting stages.
#[pyfunction]
fn integrated_loudness(
    py: Python<'_>,
    samples: PyReadonlyArray2<'_, f32>,
    rate: f64,
    stages: Vec<(Vec<f64>, Vec<f64>, f64)>,
    block: f64,
    overlap: f64,
) -> PyResult<f64> {
    if samples.shape()[1] == 0 || samples.shape()[1] > 5 {
        return Err(PyValueError::new_err("one to five channels"));
    }
    let channels = columns(&samples);
    let stages: Vec<loudness::Stage> = stages
        .into_iter()
        .map(|(b, a, gain)| loudness::Stage { b, a, gain })
        .collect();
    Ok(py.detach(|| {
        let sums = per_channel(&channels, |ch| loudness::weighted_sums(ch, &stages));
        loudness::gated(&sums, rate, block, overlap)
    }))
}

/// Frames per call of the rack's process, as a host would hand them over.
const RACK_BLOCK: usize = 512;

/// Stereo samples (rows, 2) through a rack of effects described in JSON; `side`, of the same
/// shape, is the side signal for the effects that listen to one.
#[pyfunction]
#[pyo3(signature = (samples, rate, rack_json, side=None))]
fn rack_process<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray2<'py, f32>,
    rate: f32,
    rack_json: &str,
    side: Option<PyReadonlyArray2<'py, f32>>,
) -> PyResult<Bound<'py, PyArray2<f32>>> {
    run_rack(py, samples, rate, rack_json, side, &[])
}

/// One param's curve: the slot in the rack, the param, (seconds, value) points in order of
/// time, read linearly between them.
type Lane = (usize, String, Vec<(f64, f32)>);

/// As `rack_process`, with params moving along lanes. A lane's value is set before every
/// block, at the time of the block's first frame less the latency of the effects ahead of it.
#[pyfunction]
#[pyo3(signature = (samples, rate, rack_json, lanes, side=None))]
fn rack_process_automated<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray2<'py, f32>,
    rate: f32,
    rack_json: &str,
    lanes: Vec<Lane>,
    side: Option<PyReadonlyArray2<'py, f32>>,
) -> PyResult<Bound<'py, PyArray2<f32>>> {
    run_rack(py, samples, rate, rack_json, side, &lanes)
}

fn lane_value(points: &[(f64, f32)], t: f64) -> f32 {
    let i = points.partition_point(|&(s, _)| s <= t);
    if i == 0 {
        return points[0].1;
    }
    if i == points.len() {
        return points[i - 1].1;
    }
    let ((t0, v0), (t1, v1)) = (points[i - 1], points[i]);
    let v = v0 + (v1 - v0) * ((t - t0) / (t1 - t0)) as f32;
    // rounding must not step outside the two points, which were checked against the range
    v.clamp(v0.min(v1), v0.max(v1))
}

fn check_lanes(rack: &mut Rack, lanes: &[Lane]) -> Result<(), RackError> {
    for (slot, param, points) in lanes {
        if points.is_empty() {
            return Err(RackError(format!("the lane of {param} has no points")));
        }
        let mut last = f64::NEG_INFINITY;
        for &(t, value) in points {
            if !t.is_finite() {
                return Err(RackError(format!(
                    "{param}: a point's time is a finite second, not {t}"
                )));
            }
            if t < last {
                return Err(RackError(format!("{param}: points go in order of time")));
            }
            last = t;
            rack.set(*slot, param, value)?;
        }
    }
    Ok(())
}

fn set_lanes(rack: &mut Rack, lanes: &[Lane], offsets: &[f64], frame: usize, rate: f32) {
    for ((slot, param, points), offset) in lanes.iter().zip(offsets) {
        let t = (frame as f64 - offset) / rate as f64;
        let set = rack.set(*slot, param, lane_value(points, t));
        debug_assert!(set.is_ok(), "a checked lane was refused");
    }
}

fn run_rack<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray2<'py, f32>,
    rate: f32,
    rack_json: &str,
    side: Option<PyReadonlyArray2<'py, f32>>,
    lanes: &[Lane],
) -> PyResult<Bound<'py, PyArray2<f32>>> {
    if samples.shape()[1] != 2 {
        return Err(PyValueError::new_err(
            "the rack takes stereo samples, (rows, 2)",
        ));
    }
    if let Some(s) = &side {
        if s.shape() != samples.shape() {
            return Err(PyValueError::new_err(format!(
                "the side signal is {:?}; it must have the samples' shape, {:?}",
                s.shape(),
                samples.shape()
            )));
        }
    }
    let error = |e: RackError| PyValueError::new_err(e.to_string());
    let mut rack = Rack::from_json(rack_json).map_err(error)?;
    check_lanes(&mut rack, lanes).map_err(error)?;
    rack.reset(rate).map_err(error)?;
    let offsets: Vec<f64> = lanes
        .iter()
        .map(|(slot, _, _)| rack.latency_before(*slot) as f64)
        .collect();
    if !lanes.is_empty() {
        // a reset puts the params where they are set, so they start on the lanes, not gliding
        set_lanes(&mut rack, lanes, &offsets, 0, rate);
        rack.reset(rate).map_err(error)?;
    }
    let rows = samples.shape()[0];
    // the input and then silence the length of the latency, so the output can be shifted back
    // onto the input's frames without losing its end
    let latency = rack.latency();
    let mut frames: Vec<[f32; 2]> = Vec::with_capacity(rows + latency);
    frames.extend(samples.as_array().rows().into_iter().map(|r| [r[0], r[1]]));
    frames.resize(rows + latency, [0.0; 2]);
    let side: Option<Vec<[f32; 2]>> = side.map(|s| {
        let mut v = Vec::with_capacity(rows + latency);
        v.extend(s.as_array().rows().into_iter().map(|r| [r[0], r[1]]));
        v.resize(rows + latency, [0.0; 2]);
        v
    });
    py.detach(|| {
        filters::flushing_tiny(|| match &side {
            Some(side) => {
                let blocks = frames.chunks_mut(RACK_BLOCK).zip(side.chunks(RACK_BLOCK));
                for (k, (block, s)) in blocks.enumerate() {
                    set_lanes(&mut rack, lanes, &offsets, k * RACK_BLOCK, rate);
                    rack.process_with(block, s);
                }
            }
            None => {
                for (k, block) in frames.chunks_mut(RACK_BLOCK).enumerate() {
                    set_lanes(&mut rack, lanes, &offsets, k * RACK_BLOCK, rate);
                    rack.process(block);
                }
            }
        })
    });
    let flat: Vec<f32> = frames.into_iter().skip(latency).flatten().collect();
    let arr = numpy::ndarray::Array2::from_shape_vec((rows, 2), flat)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyArray2::from_owned_array(py, arr))
}

/// The rack's effects and their params, as a JSON list.
#[pyfunction]
fn rack_effects() -> String {
    effects_json()
}

fn per_channel<T: Send>(channels: &[Vec<f32>], work: impl Fn(&[f32]) -> T + Sync) -> Vec<T> {
    std::thread::scope(|scope| {
        let handles: Vec<_> = channels
            .iter()
            .map(|ch| {
                let work = &work;
                scope.spawn(move || work(ch))
            })
            .collect();
        handles
            .into_iter()
            .map(|h| h.join().expect("a channel thread panicked"))
            .collect()
    })
}

#[pymodule]
fn redline_dsp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(oversampled_clip, m)?)?;
    m.add_function(wrap_pyfunction!(integrated_loudness, m)?)?;
    m.add_function(wrap_pyfunction!(resample, m)?)?;
    m.add_function(wrap_pyfunction!(sosfilt, m)?)?;
    m.add_function(wrap_pyfunction!(rack_process, m)?)?;
    m.add_function(wrap_pyfunction!(rack_process_automated, m)?)?;
    m.add_function(wrap_pyfunction!(rack_effects, m)?)?;
    Ok(())
}
