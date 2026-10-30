//! The reverbs Faust generates from core/faust/*.dsp (core/faust/generate.sh), with the few
//! types the generated code expects from Faust's runtime crate, which is not a dependency, and
//! one interface over them.

pub(crate) type F32 = f32;
pub(crate) type FaustFloat = f32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct ParamIndex(pub i32);

pub(crate) trait Meta {
    fn declare(&mut self, key: &str, value: &str);
}

/// Only what the generated code calls: the .dsp files use horizontal sliders alone.
pub(crate) trait UI<T> {
    fn open_vertical_box(&mut self, label: &str);
    fn close_box(&mut self);
    fn add_horizontal_slider(
        &mut self,
        label: &str,
        param: ParamIndex,
        init: T,
        min: T,
        max: T,
        step: T,
    );
}

// Faust's code, kept as Faust writes it: rustfmt does not follow include!, and the lints that
// fire on its naming and style are switched off here rather than in the generated files.
#[allow(
    clippy::all,
    non_snake_case,
    non_camel_case_types,
    non_upper_case_globals,
    unused_mut,
    unused_parens,
    unused_variables,
    dead_code
)]
mod generated {
    pub mod room {
        use super::super::*;
        include!("room.rs");
    }
    pub mod plate {
        use super::super::*;
        include!("plate.rs");
    }
    pub mod hall {
        use super::super::*;
        include!("hall.rs");
    }
    pub mod shimmer {
        use super::super::*;
        include!("shimmer.rs");
    }
}

pub(crate) use generated::hall::Hall;
pub(crate) use generated::plate::Plate;
pub(crate) use generated::room::Room;
pub(crate) use generated::shimmer::Shimmer;

/// A generated stereo DSP.
pub(crate) trait Dsp: Send + 'static {
    fn init(&mut self, rate: i32);
    fn clear(&mut self);
    fn set_param(&mut self, param: ParamIndex, value: f32);
    fn compute(&mut self, inputs: [&[f32]; 2], outputs: [&mut [f32]; 2]);
    fn interface(ui: &mut dyn UI<f32>);
}

macro_rules! dsp {
    ($($ty:ty),*) => {$(
        impl Dsp for $ty {
            fn init(&mut self, rate: i32) {
                <$ty>::init(self, rate)
            }
            fn clear(&mut self) {
                self.instance_clear()
            }
            fn set_param(&mut self, param: ParamIndex, value: f32) {
                <$ty>::set_param(self, param, value)
            }
            fn compute(&mut self, inputs: [&[f32]; 2], mut outputs: [&mut [f32]; 2]) {
                let n = inputs[0].len();
                <$ty>::compute(self, n, &inputs, &mut outputs)
            }
            fn interface(ui: &mut dyn UI<f32>) {
                <$ty>::build_user_interface_static(ui)
            }
        }
    )*};
}

dsp!(Room, Plate, Hall, Shimmer);

/// The index of a slider by its label.
pub(crate) fn param<D: Dsp>(label: &str) -> ParamIndex {
    struct Find<'a> {
        label: &'a str,
        found: Option<ParamIndex>,
    }
    impl UI<f32> for Find<'_> {
        fn open_vertical_box(&mut self, _: &str) {}
        fn close_box(&mut self) {}
        fn add_horizontal_slider(
            &mut self,
            label: &str,
            p: ParamIndex,
            _: f32,
            _: f32,
            _: f32,
            _: f32,
        ) {
            if label == self.label {
                self.found = Some(p);
            }
        }
    }
    let mut find = Find { label, found: None };
    D::interface(&mut find);
    find.found
        .unwrap_or_else(|| panic!("the generated DSP has no slider {label}"))
}

/// A generated DSP on the heap, all zeros as its `new` would make it. Made on the stack and
/// then boxed, a reverb's megabytes of delay lines can overflow a thread's stack.
pub(crate) fn boxed<D: Dsp>() -> Box<D> {
    // SAFETY: the generated structs hold only f32 and i32 fields and arrays of them, for which
    // all zero bits are a valid value; `new` sets every one of them to zero too.
    unsafe { Box::<D>::new_zeroed().assume_init() }
}
