//! process() and set() must not allocate or free once reset() has sized everything.

use redline_dsp_core::effect::{make, Effect, Rack, EFFECTS};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

struct Counting;

thread_local! {
    // per thread, so tests running side by side do not count each other
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}

fn count() {
    let _ = ALLOCATIONS.try_with(|c| c.set(c.get() + 1));
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, new_size) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

/// Calls into the allocator while f runs, frees included.
fn allocations(f: impl FnOnce()) -> usize {
    let before = ALLOCATIONS.with(Cell::get);
    f();
    ALLOCATIONS.with(Cell::get) - before
}

// signalsmith-stretch's C++ allocates through malloc and operator new, which the global
// allocator never sees. Defined in this executable, these take the place of glibc's and
// libstdc++'s for every library in the process, so C++ allocations and frees are counted too.
#[cfg(all(target_os = "linux", target_env = "gnu"))]
mod libc_counting {
    use std::ffi::c_void;

    extern "C" {
        fn __libc_malloc(size: usize) -> *mut c_void;
        fn __libc_calloc(n: usize, size: usize) -> *mut c_void;
        fn __libc_realloc(ptr: *mut c_void, size: usize) -> *mut c_void;
        fn __libc_memalign(align: usize, size: usize) -> *mut c_void;
        fn __libc_valloc(size: usize) -> *mut c_void;
        fn __libc_pvalloc(size: usize) -> *mut c_void;
        fn __libc_free(ptr: *mut c_void);
    }

    #[no_mangle]
    pub unsafe extern "C" fn malloc(size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_malloc(size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn calloc(n: usize, size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_calloc(n, size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn realloc(ptr: *mut c_void, size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_realloc(ptr, size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn free(ptr: *mut c_void) {
        if !ptr.is_null() {
            super::count();
        }
        unsafe { __libc_free(ptr) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn memalign(align: usize, size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_memalign(align, size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn aligned_alloc(align: usize, size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_memalign(align, size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn valloc(size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_valloc(size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn pvalloc(size: usize) -> *mut c_void {
        super::count();
        unsafe { __libc_pvalloc(size) }
    }

    #[no_mangle]
    pub unsafe extern "C" fn posix_memalign(
        out: *mut *mut c_void,
        align: usize,
        size: usize,
    ) -> i32 {
        super::count();
        let p = unsafe { __libc_memalign(align, size) };
        if p.is_null() {
            return 12; // ENOMEM
        }
        unsafe { *out = p };
        0
    }

    // operator delete, plain, sized and aligned, for objects and arrays; libstdc++'s own
    // operator new goes through malloc or aligned_alloc above
    macro_rules! delete {
        ($($name:ident($($arg:ty),*);)*) => {$(
            #[no_mangle]
            pub unsafe extern "C" fn $name(ptr: *mut c_void $(, _: $arg)*) {
                unsafe { free(ptr) }
            }
        )*};
    }

    delete! {
        _ZdlPv();
        _ZdlPvm(usize);
        _ZdlPvSt11align_val_t(usize);
        _ZdlPvmSt11align_val_t(usize, usize);
        _ZdaPv();
        _ZdaPvm(usize);
        _ZdaPvSt11align_val_t(usize);
        _ZdaPvmSt11align_val_t(usize, usize);
    }
}

#[test]
fn the_counter_sees_an_allocation_and_a_free() {
    assert!(allocations(|| drop(std::hint::black_box(vec![0u8; 16]))) >= 2);
    let v = std::hint::black_box(vec![0u8; 16]);
    assert!(allocations(|| drop(v)) >= 1);
}

#[cfg(all(target_os = "linux", target_env = "gnu"))]
#[test]
fn the_counter_sees_an_allocation_and_a_free_in_cpp() {
    use redline_dsp_core::stretch::Stretch;
    let mut s = Stretch::new(2, 48000.0, 1);
    // resetting reuses the buffers configure made
    assert_eq!(allocations(|| s.reset()), 0);
    let n = allocations(|| drop(std::hint::black_box(Stretch::new(2, 48000.0, 1))));
    assert!(n >= 2, "{n}");
    // operator delete and the frees of the C++ vectors
    let freed = allocations(|| drop(s));
    assert!(freed >= 2, "{freed}");
}

fn tone(n: usize) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let v = 0.5 * (i as f32 * 0.0577).sin();
            [v, 0.8 * v]
        })
        .collect()
}

/// Enough tone, in blocks of 512, for the spectral effects to analyse and synthesise blocks.
fn play(e: &mut dyn Effect, tone: &[[f32; 2]], what: &str) {
    let mut block = [[0.0f32; 2]; 512];
    for _ in 0..30 {
        block.copy_from_slice(tone);
        let n = allocations(|| e.process(&mut block));
        assert_eq!(n, 0, "{what}, on a tone");
    }
}

#[test]
fn no_effect_allocates_in_process_at_any_setting() {
    let mut block = vec![[0.3f32, -0.2]; 512];
    let tone = tone(512);
    for name in EFFECTS {
        let mut e = make(name).unwrap();
        let specs = e.params();
        // every param at its low, default and high end, the others at their defaults
        for p in specs {
            for value in [p.low, p.default, p.high] {
                e.set(p.name, value).unwrap();
                e.reset(48000.0);
                for size in [1, 64, 512] {
                    let n = allocations(|| e.process(&mut block[..size]));
                    assert_eq!(n, 0, "{name} with {} at {value}, block {size}", p.name);
                }
                play(
                    e.as_mut(),
                    &tone,
                    &format!("{name} with {} at {value}", p.name),
                );
            }
            e.set(p.name, p.default).unwrap();
        }
    }
}

#[test]
fn no_effect_allocates_in_set_or_process_while_playing() {
    let tone = tone(512);
    for name in EFFECTS {
        let mut e = make(name).unwrap();
        e.reset(48000.0);
        play(e.as_mut(), &tone, name);
        for p in e.params() {
            for value in [p.low, p.high, p.default] {
                let n = allocations(|| e.set(p.name, value).unwrap());
                assert_eq!(n, 0, "{name}: set {} to {value}", p.name);
                play(
                    e.as_mut(),
                    &tone,
                    &format!("{name} after {} = {value}", p.name),
                );
            }
        }
    }
}

#[test]
fn no_effect_allocates_coming_out_of_silence() {
    let tone = tone(512);
    let silence = [[0.0f32; 2]; 512];
    for name in EFFECTS {
        let mut e = make(name).unwrap();
        e.reset(48000.0);
        // half a second: longer than the two blocks after which the stretcher idles
        for _ in 0..47 {
            let mut block = silence;
            assert_eq!(allocations(|| e.process(&mut block)), 0, "{name}, silent");
        }
        play(e.as_mut(), &tone, &format!("{name} after silence"));
    }
}

#[test]
fn a_rack_of_every_effect_does_not_allocate_in_process() {
    let effects: Vec<String> = EFFECTS
        .iter()
        .map(|e| format!(r#"{{"effect": "{e}"}}"#))
        .collect();
    let mut rack = Rack::from_json(&format!(r#"{{"effects": [{}]}}"#, effects.join(","))).unwrap();
    rack.reset(44100.0).unwrap();
    let mut block = vec![[0.1f32, 0.4]; 512];
    for size in [1, 64, 512] {
        assert_eq!(
            allocations(|| rack.process(&mut block[..size])),
            0,
            "block {size}"
        );
    }
    let tone = tone(512);
    for _ in 0..30 {
        block.copy_from_slice(&tone);
        assert_eq!(allocations(|| rack.process(&mut block)), 0, "on a tone");
    }
}

#[test]
fn no_effect_allocates_taking_a_side_signal() {
    let tone = tone(512);
    let side: Vec<[f32; 2]> = tone.iter().map(|f| [f[1], -f[0]]).collect();
    for name in EFFECTS {
        let mut e = make(name).unwrap();
        e.reset(48000.0);
        let mut block = [[0.0f32; 2]; 512];
        for _ in 0..30 {
            block.copy_from_slice(&tone);
            let n = allocations(|| {
                e.sidechain(&side);
                e.process(&mut block);
            });
            assert_eq!(n, 0, "{name}, with a side");
        }
    }
    let effects: Vec<String> = EFFECTS
        .iter()
        .map(|e| format!(r#"{{"effect": "{e}"}}"#))
        .collect();
    let mut rack = Rack::from_json(&format!(r#"{{"effects": [{}]}}"#, effects.join(","))).unwrap();
    rack.reset(44100.0).unwrap();
    // longer than the longest block a side comes with: the rack splits it
    let mut long = vec![[0.1f32, 0.4]; 9000];
    let long_side = vec![[0.3f32, -0.2]; 9000];
    assert_eq!(
        allocations(|| rack.process_with(&mut long, &long_side)),
        0,
        "a rack with a side"
    );
}
