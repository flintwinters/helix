//! Allocation accounting for versioned execution; one test keeps measurements isolated.
use helix::{Status, Vm};
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

struct Accounting;
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);
fn allocated(size: usize) {
    let live = LIVE.fetch_add(size, Ordering::Relaxed) + size;
    PEAK.fetch_max(live, Ordering::Relaxed);
}
unsafe impl GlobalAlloc for Accounting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            allocated(layout.size());
        }
        pointer
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
        unsafe { System.dealloc(pointer, layout) }
    }
    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        let replacement = unsafe { System.realloc(pointer, layout, size) };
        if !replacement.is_null() {
            LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
            allocated(size);
        }
        replacement
    }
}
#[global_allocator]
static ALLOCATOR: Accounting = Accounting;

fn workload(iterations: usize, stepped: bool, retain: bool) -> (usize, usize) {
    let baseline = LIVE.load(Ordering::Relaxed);
    PEAK.store(baseline, Ordering::Relaxed);
    let source = format!("[until, [fn, [n], [ge, n, {iterations}]], [fn, [n], [add, n, 1]], 0]");
    let ready = Vm::load(&source);
    let saved = ready.inspect();
    let mut vm = ready.clone();
    let mut snapshots = Vec::new();
    let mut steps = 0;
    let started = Instant::now();
    if stepped {
        while matches!(vm.status(), Status::Ready | Status::Running) {
            if retain && snapshots.len() < 16 && steps % 307 == 0 {
                snapshots.push(vm.clone());
            }
            vm = vm.step();
            steps += 1;
            assert!(steps < iterations * 100 + 100);
        }
    } else {
        vm.run();
    }
    let elapsed = started.elapsed();
    let live = LIVE.load(Ordering::Relaxed).saturating_sub(baseline);
    let peak = PEAK.load(Ordering::Relaxed).saturating_sub(baseline);
    assert_eq!(vm.status(), Status::Finished);
    assert_eq!(vm.state()["root"].as_i64(), Some(iterations as i64));
    assert_eq!(ready.inspect(), saved, "the retained ready version changed");
    for old in snapshots {
        let mut branch = old;
        branch.run();
        assert_eq!(branch, vm);
    }
    println!(
        "execution-profile: iterations={iterations} stepped={stepped} retained={retain} transitions={steps} live_bytes={live} peak_bytes={peak} seconds={:.4}",
        elapsed.as_secs_f64()
    );
    assert!(
        elapsed.as_secs_f64() < 20.0,
        "bounded execution exceeded its generous regression budget"
    );
    (live, peak)
}
#[test]
fn versions_share_code_and_do_not_retain_execution_history() {
    let (small, _) = workload(200, false, false);
    let (large, peak) = workload(2000, false, false);
    let (stepped, step_peak) = workload(2000, true, false);
    let (retained, retain_peak) = workload(2000, true, true);
    assert!(
        large < small + 128 * 1024,
        "run retained iteration history: {small} -> {large}"
    );
    assert!(
        stepped < large + 128 * 1024,
        "step retained discarded versions: {large} -> {stepped}"
    );
    assert!(
        retained < stepped + 512 * 1024,
        "bounded checkpoints retained excessive metadata"
    );
    assert!(
        peak < 2 * 1024 * 1024 && step_peak < 2 * 1024 * 1024 && retain_peak < 4 * 1024 * 1024,
        "unexpected transient copying"
    );
}
