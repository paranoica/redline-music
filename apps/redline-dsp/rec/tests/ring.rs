use redline_rec::ring::ring;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

#[test]
fn items_come_out_in_order_and_a_full_ring_hands_the_item_back() {
    let (mut tx, mut rx) = ring::<u32>(4);
    assert_eq!(tx.capacity(), 4);
    assert_eq!(rx.pop(), None);
    for v in 0..4 {
        tx.push(v).unwrap();
    }
    assert_eq!(tx.push(9), Err(9));
    assert_eq!(rx.len(), 4);
    assert_eq!(rx.pop(), Some(0));
    tx.push(4).unwrap();
    assert_eq!(
        (0..4).map(|_| rx.pop().unwrap()).collect::<Vec<_>>(),
        [1, 2, 3, 4]
    );
    assert_eq!(rx.pop(), None);
}

#[test]
fn the_capacity_rounds_up_to_a_power_of_two() {
    assert_eq!(ring::<u8>(5).0.capacity(), 8);
    assert_eq!(ring::<u8>(1).0.capacity(), 1);
    assert_eq!(ring::<u8>(0).0.capacity(), 1);
}

#[test]
fn a_slice_goes_in_whole_or_not_at_all_and_wraps_around_the_end() {
    let (mut tx, mut rx) = ring::<f32>(8);
    assert!(tx.push_slice(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]));
    let mut out = [0.0; 4];
    assert_eq!(rx.pop_slice(&mut out), 4);
    assert_eq!(out, [1.0, 2.0, 3.0, 4.0]);
    // 2 left, 6 free: 7 do not fit and nothing goes in
    assert_eq!(tx.free(), 6);
    assert!(!tx.push_slice(&[0.0; 7]));
    assert_eq!(rx.len(), 2);
    // 6 fit, across the end of the buffer
    assert!(tx.push_slice(&[7.0, 8.0, 9.0, 10.0, 11.0, 12.0]));
    assert_eq!(tx.free(), 0);
    let mut out = [0.0; 10];
    assert_eq!(rx.pop_slice(&mut out), 8);
    assert_eq!(&out[..8], &[5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]);
    assert_eq!(rx.pop_slice(&mut out), 0);
}

#[test]
fn a_producer_and_a_consumer_on_two_threads_pass_every_item_in_order() {
    let n = 200_000u32;
    let (mut tx, mut rx) = ring::<u32>(64);
    let producer = std::thread::spawn(move || {
        let mut v = 0;
        while v < n {
            if v % 3 == 0 {
                let block = [v, v + 1, v + 2];
                if v + 2 < n && tx.push_slice(&block) {
                    v += 3;
                    continue;
                }
            }
            if tx.push(v).is_ok() {
                v += 1;
            }
        }
    });
    let mut want = 0;
    let mut buf = [0u32; 7];
    while want < n {
        let got = rx.pop_slice(&mut buf);
        for &v in &buf[..got] {
            assert_eq!(v, want);
            want += 1;
        }
        if got == 0 {
            std::hint::spin_loop();
        }
    }
    producer.join().unwrap();
    assert_eq!(rx.pop(), None);
}

struct Counted(Arc<AtomicUsize>);

impl Drop for Counted {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}

#[test]
fn items_left_in_the_ring_are_dropped_with_it_and_popped_ones_once() {
    let drops = Arc::new(AtomicUsize::new(0));
    let (mut tx, mut rx) = ring::<Counted>(4);
    for _ in 0..3 {
        assert!(tx.push(Counted(drops.clone())).is_ok());
    }
    drop(rx.pop());
    assert_eq!(drops.load(Ordering::SeqCst), 1);
    drop(tx);
    assert_eq!(
        drops.load(Ordering::SeqCst),
        1,
        "the consumer still holds the ring"
    );
    drop(rx);
    assert_eq!(drops.load(Ordering::SeqCst), 3);
}
