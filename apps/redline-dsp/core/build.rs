fn main() {
    println!("cargo:rerun-if-changed=cpp");
    println!("cargo:rerun-if-changed=vendor");
    cc::Build::new()
        .cpp(true)
        .std("c++11")
        .file("cpp/stretch.cpp")
        .include("vendor")
        // the spectral loops are unusably slow unoptimised, so debug builds get -O2 too
        .opt_level(2)
        .compile("redline_stretch");
}
