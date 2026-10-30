//! Once a rack is made, processing it and setting a param must not touch the allocator: the
//! browser calls them on the audio thread. The C++ inside the core is counted by the core's
//! own realtime test; this one counts what the C ABI adds around it.

use redline_dsp_wasm::{rack_free, rack_new, rack_process, rack_set};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::ptr;

struct Counting;

thread_local! {
    // per thread: the test harness's own thread allocates while the test runs
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

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

#[test]
fn processing_and_setting_do_not_allocate() {
    let json = r#"{"effects": [{"effect": "gain"}, {"effect": "pitch", "params": {"semitones": 7}}, {"effect": "duck"}]}"#;
    let h = unsafe { rack_new(48000.0, json.as_ptr(), json.len()) };
    assert_ne!(h, 0);
    let input: Vec<f32> = (0..256).map(|n| (n as f32 * 0.05).sin()).collect();
    let side = vec![0.5f32; 256];
    let mut out = vec![0.0f32; 256];
    let before = ALLOCATIONS.with(Cell::get);
    for k in 0..400 {
        let s = if k % 2 == 0 {
            side.as_ptr()
        } else {
            ptr::null()
        };
        let code = unsafe { rack_process(h, input.as_ptr(), s, out.as_mut_ptr(), 128) };
        assert_eq!(code, 0);
        let db = -((k % 24) as f32);
        assert_eq!(unsafe { rack_set(h, 0, "db".as_ptr(), 2, db) }, 0);
        assert_eq!(unsafe { rack_set(h, 2, "depth".as_ptr(), 5, 6.0) }, 0);
    }
    assert_eq!(ALLOCATIONS.with(Cell::get) - before, 0);
    rack_free(h);
}
