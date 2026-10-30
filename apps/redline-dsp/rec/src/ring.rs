//! A lock-free ring for one producer and one consumer, sized once: pushing and popping never
//! allocate, lock or wait, so the audio thread can use either end.

use std::cell::UnsafeCell;
use std::mem::MaybeUninit;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

struct Shared<T> {
    slots: Box<[UnsafeCell<MaybeUninit<T>>]>,
    mask: usize,
    // both count up forever, wrapping; head - tail is how many are in the ring
    head: AtomicUsize,
    tail: AtomicUsize,
}

// one thread writes a slot, then hands it over through `head` (or back through `tail`)
unsafe impl<T: Send> Sync for Shared<T> {}

impl<T> Drop for Shared<T> {
    fn drop(&mut self) {
        let head = *self.head.get_mut();
        let mut tail = *self.tail.get_mut();
        while tail != head {
            unsafe { self.slots[tail & self.mask].get_mut().assume_init_drop() };
            tail = tail.wrapping_add(1);
        }
    }
}

pub struct Producer<T> {
    shared: Arc<Shared<T>>,
}

pub struct Consumer<T> {
    shared: Arc<Shared<T>>,
}

/// A ring holding `capacity` items, rounded up to a power of two.
pub fn ring<T: Send>(capacity: usize) -> (Producer<T>, Consumer<T>) {
    let capacity = capacity.max(1).next_power_of_two();
    let slots = (0..capacity)
        .map(|_| UnsafeCell::new(MaybeUninit::uninit()))
        .collect();
    let shared = Arc::new(Shared {
        slots,
        mask: capacity - 1,
        head: AtomicUsize::new(0),
        tail: AtomicUsize::new(0),
    });
    (
        Producer {
            shared: shared.clone(),
        },
        Consumer { shared },
    )
}

impl<T: Send> Producer<T> {
    pub fn capacity(&self) -> usize {
        self.shared.slots.len()
    }

    /// Room left.
    pub fn free(&self) -> usize {
        let s = &*self.shared;
        let head = s.head.load(Ordering::Relaxed);
        self.capacity() - head.wrapping_sub(s.tail.load(Ordering::Acquire))
    }

    /// The item back when the ring is full.
    pub fn push(&mut self, item: T) -> Result<(), T> {
        if self.free() == 0 {
            return Err(item);
        }
        let s = &*self.shared;
        let head = s.head.load(Ordering::Relaxed);
        unsafe { (*s.slots[head & s.mask].get()).write(item) };
        s.head.store(head.wrapping_add(1), Ordering::Release);
        Ok(())
    }
}

impl<T: Send + Copy> Producer<T> {
    /// All of `items`, or none of them when they do not fit.
    pub fn push_slice(&mut self, items: &[T]) -> bool {
        if items.len() > self.free() {
            return false;
        }
        let s = &*self.shared;
        let head = s.head.load(Ordering::Relaxed);
        for (i, &item) in items.iter().enumerate() {
            unsafe { (*s.slots[head.wrapping_add(i) & s.mask].get()).write(item) };
        }
        s.head
            .store(head.wrapping_add(items.len()), Ordering::Release);
        true
    }
}

impl<T: Send> Consumer<T> {
    /// Items waiting.
    pub fn len(&self) -> usize {
        let s = &*self.shared;
        s.head
            .load(Ordering::Acquire)
            .wrapping_sub(s.tail.load(Ordering::Relaxed))
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn pop(&mut self) -> Option<T> {
        if self.is_empty() {
            return None;
        }
        let s = &*self.shared;
        let tail = s.tail.load(Ordering::Relaxed);
        let item = unsafe { (*s.slots[tail & s.mask].get()).assume_init_read() };
        s.tail.store(tail.wrapping_add(1), Ordering::Release);
        Some(item)
    }
}

impl<T: Send + Copy> Consumer<T> {
    /// As many items as are waiting, up to `out.len()`; how many.
    pub fn pop_slice(&mut self, out: &mut [T]) -> usize {
        let n = self.len().min(out.len());
        let s = &*self.shared;
        let tail = s.tail.load(Ordering::Relaxed);
        for (i, v) in out[..n].iter_mut().enumerate() {
            *v = unsafe { (*s.slots[tail.wrapping_add(i) & s.mask].get()).assume_init_read() };
        }
        s.tail.store(tail.wrapping_add(n), Ordering::Release);
        n
    }
}
