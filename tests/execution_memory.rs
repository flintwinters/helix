//! Allocation accounting for versioned execution; one test keeps measurements isolated.
use helix::{EffectHost, Status, Vm};
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

struct Accounting;
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);
static TOTAL: AtomicUsize = AtomicUsize::new(0);
fn allocated(size: usize) {
    TOTAL.fetch_add(size, Ordering::Relaxed);
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

fn host_request_allocations(bytes: usize) -> usize {
    // Yield before measuring so source parsing and request construction do not
    // obscure the host boundary's payload-copy cost. A rejected exit has no
    // external action but still records its payload and structured failure.
    let mut vm = Vm::load(&format!("[effect, 'exit', '{}']", "x".repeat(bytes)));
    vm.run();
    assert_eq!(vm.status(), Status::Signaled);
    let baseline = TOTAL.load(Ordering::Relaxed);
    EffectHost::default().run(&mut vm);
    let allocated = TOTAL.load(Ordering::Relaxed).saturating_sub(baseline);
    assert_eq!(vm.status(), Status::Error);
    assert_eq!(vm.state()["error"]["kind"].as_str(), Some("type"));
    println!("host-request-profile: payload_bytes={bytes} allocated_bytes={allocated}");
    allocated
}
fn call_argument_allocations(count: usize) -> usize {
    let parameters = (0..count)
        .map(|i| format!("p{i}"))
        .collect::<Vec<_>>()
        .join(", ");
    let arguments = std::iter::repeat_n("0", count)
        .collect::<Vec<_>>()
        .join(", ");
    let mut vm = Vm::load(&format!("[call, [fn, [{parameters}], 7], {arguments}]"));
    assert_eq!(vm.status(), Status::Ready);
    let baseline = TOTAL.load(Ordering::Relaxed);
    vm.run();
    let allocated = TOTAL.load(Ordering::Relaxed).saturating_sub(baseline);
    assert_eq!(vm.status(), Status::Finished);
    assert_eq!(vm.state()["root"].as_i64(), Some(7));
    println!("call-argument-profile: arguments={count} allocated_bytes={allocated}");
    allocated
}

#[test]
fn versions_share_code_and_do_not_retain_execution_history() {
    let small_call = call_argument_allocations(128);
    let large_call = call_argument_allocations(512);
    assert!(
        large_call <= 6 * small_call + 4096,
        "call argument scheduling allocated quadratically: {small_call} -> {large_call}"
    );
    let small_request = host_request_allocations(32 * 1024);
    let large_request = host_request_allocations(256 * 1024);
    assert!(
        large_request.saturating_sub(small_request) <= 3 * (256 - 32) * 1024 + 16 * 1024,
        "host dispatch introduced an extra full-payload copy: {small_request} -> {large_request}"
    );
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
