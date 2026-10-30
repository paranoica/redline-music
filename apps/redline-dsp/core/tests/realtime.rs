//! process() must not allocate once reset() has sized everything.

use redline_dsp_core::effect::{make, Rack, EFFECTS};
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
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

fn allocations(f: impl FnOnce()) -> usize {
    let before = ALLOCATIONS.with(Cell::get);
    f();
    ALLOCATIONS.with(Cell::get) - before
}

#[test]
fn the_counter_sees_an_allocation() {
    assert!(allocations(|| drop(std::hint::black_box(vec![0u8; 16]))) >= 1);
}

#[test]
fn no_effect_allocates_in_process_at_any_setting() {
    let mut block = vec![[0.3f32, -0.2]; 512];
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
            }
            e.set(p.name, p.default).unwrap();
        }
    }
}

#[test]
fn a_rack_of_every_effect_does_not_allocate_in_process() {
    let effects: Vec<String> = EFFECTS
        .iter()
        .map(|e| format!(r#"{{"effect": "{e}"}}"#))
        .collect();
    let mut rack = Rack::from_json(&format!(r#"{{"effects": [{}]}}"#, effects.join(","))).unwrap();
    rack.reset(44100.0);
    let mut block = vec![[0.1f32, 0.4]; 512];
    for size in [1, 64, 512] {
        assert_eq!(
            allocations(|| rack.process(&mut block[..size])),
            0,
            "block {size}"
        );
    }
}
