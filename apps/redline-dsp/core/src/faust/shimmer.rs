// Generated from core/faust/shimmer.dsp by core/faust/generate.sh; do not edit.
/* ------------------------------------------------------------
name: "shimmer"
Code generated with Faust 2.88.0 (https://faust.grame.fr)
Compilation options: -lang rust -fpga-mem-th 4 -light -ct 1 -cn Shimmer -es 1 -mcd 16 -mdd 1024 -mdy 33 -single -ftz 2
------------------------------------------------------------ */

#[repr(C)]
pub struct Shimmer {
	fSampleRate: i32,
	fConst0: F32,
	fConst1: F32,
	fConst2: F32,
	fConst3: F32,
	fRec0: [F32;2],
	fRec1: [F32;2],
	fRec2: [F32;2],
	fHslider0: FaustFloat,
	fConst4: F32,
	fConst5: F32,
	fHslider1: FaustFloat,
	fConst6: F32,
	fRec3: [F32;2],
	fRec4: [F32;2],
	fRec5: [F32;2],
	fRec6: [F32;2],
	fConst7: F32,
	fConst8: F32,
	fRec7: [F32;2],
	fHslider2: FaustFloat,
	fRec42: [F32;2],
	IOTA0: i32,
	fHslider3: FaustFloat,
	fVec0: [F32;32768],
	fConst9: F32,
	iConst10: i32,
	fVec1: [F32;8192],
	iConst11: i32,
	fRec8: [F32;2],
	fRec9: [F32;2],
	fConst12: F32,
	fConst13: F32,
	fRec10: [F32;2],
	fRec12: [F32;2],
	fVec2: [F32;65536],
	fConst14: F32,
	iConst15: i32,
	fVec3: [F32;8192],
	iConst16: i32,
	fRec13: [F32;2],
	fVec4: [F32;65536],
	fConst17: F32,
	iConst18: i32,
	fVec5: [F32;8192],
	iConst19: i32,
	fRec15: [F32;2],
	fConst20: F32,
	fConst21: F32,
	fRec16: [F32;2],
	fConst22: F32,
	fConst23: F32,
	fRec17: [F32;2],
	fConst24: F32,
	fConst25: F32,
	fRec18: [F32;2],
	fConst26: F32,
	fConst27: F32,
	fRec19: [F32;2],
	fVec6: [F32;32768],
	fConst28: F32,
	iConst29: i32,
	fVec7: [F32;4096],
	iConst30: i32,
	fRec20: [F32;2],
	fVec8: [F32;65536],
	fConst31: F32,
	iConst32: i32,
	fVec9: [F32;4096],
	iConst33: i32,
	fRec21: [F32;2],
	fVec10: [F32;32768],
	fConst34: F32,
	iConst35: i32,
	fVec11: [F32;4096],
	iConst36: i32,
	fRec23: [F32;2],
	fConst37: F32,
	fConst38: F32,
	fRec26: [F32;2],
	fVec12: [F32;65536],
	fConst39: F32,
	iConst40: i32,
	fVec13: [F32;8192],
	fVec14: [F32;65536],
	fConst41: F32,
	iConst42: i32,
	fVec15: [F32;8192],
	iConst43: i32,
	fRec29: [F32;2],
	iConst44: i32,
	fRec31: [F32;2],
	fRec32: [F32;3],
	fRec33: [F32;3],
	fRec34: [F32;3],
	fRec35: [F32;3],
	fRec36: [F32;3],
	fRec37: [F32;3],
	fRec38: [F32;3],
	fRec39: [F32;3],
	fRec40: [F32;131072],
	fRec41: [F32;131072],
}


fn Shimmer_faustpower2_f(value: F32) -> F32 {
	return value * value;
}
pub const FAUST_INPUTS: usize = 2;
pub const FAUST_OUTPUTS: usize = 2;
pub const FAUST_ACTIVES: usize = 4;
pub const FAUST_PASSIVES: usize = 0;

impl Shimmer {
		
	pub fn new() -> Shimmer { 
		Shimmer {
			fSampleRate: 0,
			fConst0: 0.0,
			fConst1: 0.0,
			fConst2: 0.0,
			fConst3: 0.0,
			fRec0: [0.0;2],
			fRec1: [0.0;2],
			fRec2: [0.0;2],
			fHslider0: 0.0,
			fConst4: 0.0,
			fConst5: 0.0,
			fHslider1: 0.0,
			fConst6: 0.0,
			fRec3: [0.0;2],
			fRec4: [0.0;2],
			fRec5: [0.0;2],
			fRec6: [0.0;2],
			fConst7: 0.0,
			fConst8: 0.0,
			fRec7: [0.0;2],
			fHslider2: 0.0,
			fRec42: [0.0;2],
			IOTA0: 0,
			fHslider3: 0.0,
			fVec0: [0.0;32768],
			fConst9: 0.0,
			iConst10: 0,
			fVec1: [0.0;8192],
			iConst11: 0,
			fRec8: [0.0;2],
			fRec9: [0.0;2],
			fConst12: 0.0,
			fConst13: 0.0,
			fRec10: [0.0;2],
			fRec12: [0.0;2],
			fVec2: [0.0;65536],
			fConst14: 0.0,
			iConst15: 0,
			fVec3: [0.0;8192],
			iConst16: 0,
			fRec13: [0.0;2],
			fVec4: [0.0;65536],
			fConst17: 0.0,
			iConst18: 0,
			fVec5: [0.0;8192],
			iConst19: 0,
			fRec15: [0.0;2],
			fConst20: 0.0,
			fConst21: 0.0,
			fRec16: [0.0;2],
			fConst22: 0.0,
			fConst23: 0.0,
			fRec17: [0.0;2],
			fConst24: 0.0,
			fConst25: 0.0,
			fRec18: [0.0;2],
			fConst26: 0.0,
			fConst27: 0.0,
			fRec19: [0.0;2],
			fVec6: [0.0;32768],
			fConst28: 0.0,
			iConst29: 0,
			fVec7: [0.0;4096],
			iConst30: 0,
			fRec20: [0.0;2],
			fVec8: [0.0;65536],
			fConst31: 0.0,
			iConst32: 0,
			fVec9: [0.0;4096],
			iConst33: 0,
			fRec21: [0.0;2],
			fVec10: [0.0;32768],
			fConst34: 0.0,
			iConst35: 0,
			fVec11: [0.0;4096],
			iConst36: 0,
			fRec23: [0.0;2],
			fConst37: 0.0,
			fConst38: 0.0,
			fRec26: [0.0;2],
			fVec12: [0.0;65536],
			fConst39: 0.0,
			iConst40: 0,
			fVec13: [0.0;8192],
			fVec14: [0.0;65536],
			fConst41: 0.0,
			iConst42: 0,
			fVec15: [0.0;8192],
			iConst43: 0,
			fRec29: [0.0;2],
			iConst44: 0,
			fRec31: [0.0;2],
			fRec32: [0.0;3],
			fRec33: [0.0;3],
			fRec34: [0.0;3],
			fRec35: [0.0;3],
			fRec36: [0.0;3],
			fRec37: [0.0;3],
			fRec38: [0.0;3],
			fRec39: [0.0;3],
			fRec40: [0.0;131072],
			fRec41: [0.0;131072],
		}
	}
	pub fn metadata(&self, m: &mut dyn Meta) { 
		m.declare("analyzers.lib/name", r"Faust Analyzer Library");
		m.declare("analyzers.lib/version", r"1.4.0");
		m.declare("basics.lib/name", r"Faust Basic Element Library");
		m.declare("basics.lib/version", r"1.23.0");
		m.declare("compile_options", r"-lang rust -fpga-mem-th 4 -light -ct 1 -cn Shimmer -es 1 -mcd 16 -mdd 1024 -mdy 33 -single -ftz 2");
		m.declare("delays.lib/name", r"Faust Delay Library");
		m.declare("delays.lib/version", r"1.2.0");
		m.declare("filename", r"shimmer.dsp");
		m.declare("filters.lib/allpass_comb:author", r"Julius O. Smith III");
		m.declare("filters.lib/allpass_comb:copyright", r"Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m.declare("filters.lib/allpass_comb:license", r"LicenseRef-STK-4.3");
		m.declare("filters.lib/lowpass0_highpass1:author", r"Julius O. Smith III");
		m.declare("filters.lib/lowpass0_highpass1:copyright", r"Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m.declare("filters.lib/lowpass0_highpass1:license", r"LicenseRef-STK-4.3");
		m.declare("filters.lib/lowpass:author", r"Julius O. Smith III");
		m.declare("filters.lib/lowpass:copyright", r"Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m.declare("filters.lib/lowpass:license", r"LicenseRef-STK-4.3");
		m.declare("filters.lib/name", r"Faust Filters Library");
		m.declare("filters.lib/tf1:author", r"Julius O. Smith III");
		m.declare("filters.lib/tf1:copyright", r"Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m.declare("filters.lib/tf1:license", r"LicenseRef-STK-4.3");
		m.declare("filters.lib/tf1s:author", r"Julius O. Smith III");
		m.declare("filters.lib/tf1s:copyright", r"Copyright (C) 2003-2019 by Julius O. Smith III <jos@ccrma.stanford.edu>");
		m.declare("filters.lib/tf1s:license", r"LicenseRef-STK-4.3");
		m.declare("filters.lib/version", r"1.9.0");
		m.declare("maths.lib/author", r"GRAME");
		m.declare("maths.lib/copyright", r"GRAME");
		m.declare("maths.lib/license", r"LicenseRef-LGPL-2.1-or-later-with-Faust-exception");
		m.declare("maths.lib/name", r"Faust Math Library");
		m.declare("maths.lib/version", r"2.9.0");
		m.declare("misceffects.lib/name", r"Misc Effects Library");
		m.declare("misceffects.lib/transpose_windowed:license", r"MIT");
		m.declare("misceffects.lib/version", r"2.6.0");
		m.declare("name", r"shimmer");
		m.declare("platform.lib/name", r"Generic Platform Library");
		m.declare("platform.lib/version", r"1.3.0");
		m.declare("reverbs.lib/name", r"Faust Reverb Library");
		m.declare("reverbs.lib/version", r"1.5.1");
		m.declare("routes.lib/hadamard:author", r"Remy Muller, revised by Romain Michon");
		m.declare("routes.lib/name", r"Faust Signal Routing Library");
		m.declare("routes.lib/version", r"1.4.0");
		m.declare("signals.lib/name", r"Faust Routing Library");
		m.declare("signals.lib/version", r"1.7.0");
	}

	pub fn get_sample_rate(&self) -> i32 { self.fSampleRate as i32}
	
	pub fn class_init(sample_rate: i32) {
		// Obtaining locks on 0 static var(s)
	}
	pub fn instance_reset_params(&mut self) {
		self.fHslider0 = (4.0) as FaustFloat;
		self.fHslider1 = (8e+03) as FaustFloat;
		self.fHslider2 = (12.0) as FaustFloat;
		self.fHslider3 = (0.5) as FaustFloat;
	}
	pub fn instance_clear(&mut self) {
		for l0 in 0..2 {
			self.fRec0[l0 as usize] = 0.0;
		}
		for l1 in 0..2 {
			self.fRec1[l1 as usize] = 0.0;
		}
		for l2 in 0..2 {
			self.fRec2[l2 as usize] = 0.0;
		}
		for l3 in 0..2 {
			self.fRec3[l3 as usize] = 0.0;
		}
		for l4 in 0..2 {
			self.fRec4[l4 as usize] = 0.0;
		}
		for l5 in 0..2 {
			self.fRec5[l5 as usize] = 0.0;
		}
		for l6 in 0..2 {
			self.fRec6[l6 as usize] = 0.0;
		}
		for l7 in 0..2 {
			self.fRec7[l7 as usize] = 0.0;
		}
		for l8 in 0..2 {
			self.fRec42[l8 as usize] = 0.0;
		}
		self.IOTA0 = 0;
		for l9 in 0..32768 {
			self.fVec0[l9 as usize] = 0.0;
		}
		for l10 in 0..8192 {
			self.fVec1[l10 as usize] = 0.0;
		}
		for l11 in 0..2 {
			self.fRec8[l11 as usize] = 0.0;
		}
		for l12 in 0..2 {
			self.fRec9[l12 as usize] = 0.0;
		}
		for l13 in 0..2 {
			self.fRec10[l13 as usize] = 0.0;
		}
		for l14 in 0..2 {
			self.fRec12[l14 as usize] = 0.0;
		}
		for l15 in 0..65536 {
			self.fVec2[l15 as usize] = 0.0;
		}
		for l16 in 0..8192 {
			self.fVec3[l16 as usize] = 0.0;
		}
		for l17 in 0..2 {
			self.fRec13[l17 as usize] = 0.0;
		}
		for l18 in 0..65536 {
			self.fVec4[l18 as usize] = 0.0;
		}
		for l19 in 0..8192 {
			self.fVec5[l19 as usize] = 0.0;
		}
		for l20 in 0..2 {
			self.fRec15[l20 as usize] = 0.0;
		}
		for l21 in 0..2 {
			self.fRec16[l21 as usize] = 0.0;
		}
		for l22 in 0..2 {
			self.fRec17[l22 as usize] = 0.0;
		}
		for l23 in 0..2 {
			self.fRec18[l23 as usize] = 0.0;
		}
		for l24 in 0..2 {
			self.fRec19[l24 as usize] = 0.0;
		}
		for l25 in 0..32768 {
			self.fVec6[l25 as usize] = 0.0;
		}
		for l26 in 0..4096 {
			self.fVec7[l26 as usize] = 0.0;
		}
		for l27 in 0..2 {
			self.fRec20[l27 as usize] = 0.0;
		}
		for l28 in 0..65536 {
			self.fVec8[l28 as usize] = 0.0;
		}
		for l29 in 0..4096 {
			self.fVec9[l29 as usize] = 0.0;
		}
		for l30 in 0..2 {
			self.fRec21[l30 as usize] = 0.0;
		}
		for l31 in 0..32768 {
			self.fVec10[l31 as usize] = 0.0;
		}
		for l32 in 0..4096 {
			self.fVec11[l32 as usize] = 0.0;
		}
		for l33 in 0..2 {
			self.fRec23[l33 as usize] = 0.0;
		}
		for l34 in 0..2 {
			self.fRec26[l34 as usize] = 0.0;
		}
		for l35 in 0..65536 {
			self.fVec12[l35 as usize] = 0.0;
		}
		for l36 in 0..8192 {
			self.fVec13[l36 as usize] = 0.0;
		}
		for l37 in 0..65536 {
			self.fVec14[l37 as usize] = 0.0;
		}
		for l38 in 0..8192 {
			self.fVec15[l38 as usize] = 0.0;
		}
		for l39 in 0..2 {
			self.fRec29[l39 as usize] = 0.0;
		}
		for l40 in 0..2 {
			self.fRec31[l40 as usize] = 0.0;
		}
		for l41 in 0..3 {
			self.fRec32[l41 as usize] = 0.0;
		}
		for l42 in 0..3 {
			self.fRec33[l42 as usize] = 0.0;
		}
		for l43 in 0..3 {
			self.fRec34[l43 as usize] = 0.0;
		}
		for l44 in 0..3 {
			self.fRec35[l44 as usize] = 0.0;
		}
		for l45 in 0..3 {
			self.fRec36[l45 as usize] = 0.0;
		}
		for l46 in 0..3 {
			self.fRec37[l46 as usize] = 0.0;
		}
		for l47 in 0..3 {
			self.fRec38[l47 as usize] = 0.0;
		}
		for l48 in 0..3 {
			self.fRec39[l48 as usize] = 0.0;
		}
		for l49 in 0..131072 {
			self.fRec40[l49 as usize] = 0.0;
		}
		for l50 in 0..131072 {
			self.fRec41[l50 as usize] = 0.0;
		}
	}
	pub fn instance_constants(&mut self, sample_rate: i32) {
		// Obtaining locks on 0 static var(s)
		self.fSampleRate = sample_rate;
		self.fConst0 = F32::min(1.92e+05, F32::max(1.0, (self.fSampleRate) as F32));
		self.fConst1 = 1.0 / F32::tan(628.31854 / self.fConst0);
		self.fConst2 = 1.0 - self.fConst1;
		self.fConst3 = 1.0 / (self.fConst1 + 1.0);
		self.fConst4 = F32::floor(0.127837 * self.fConst0 + 0.5);
		self.fConst5 = 6.9077554 * (self.fConst4 / self.fConst0);
		self.fConst6 = 6.2831855 / self.fConst0;
		self.fConst7 = F32::floor(0.256891 * self.fConst0 + 0.5);
		self.fConst8 = 6.9077554 * (self.fConst7 / self.fConst0);
		self.fConst9 = F32::floor(0.031604 * self.fConst0 + 0.5);
		self.iConst10 = (F32::min(32768.0, F32::max(0.0, self.fConst4 - self.fConst9))) as i32;
		self.iConst11 = (F32::min(8192.0, F32::max(0.0, self.fConst9 + -1.0))) as i32;
		self.fConst12 = F32::floor(0.210389 * self.fConst0 + 0.5);
		self.fConst13 = 6.9077554 * (self.fConst12 / self.fConst0);
		self.fConst14 = F32::floor(0.027333 * self.fConst0 + 0.5);
		self.iConst15 = (F32::min(65536.0, F32::max(0.0, self.fConst7 - self.fConst14))) as i32;
		self.iConst16 = (F32::min(8192.0, F32::max(0.0, self.fConst14 + -1.0))) as i32;
		self.fConst17 = F32::floor(0.024421 * self.fConst0 + 0.5);
		self.iConst18 = (F32::min(65536.0, F32::max(0.0, self.fConst12 - self.fConst17))) as i32;
		self.iConst19 = (F32::min(8192.0, F32::max(0.0, self.fConst17 + -1.0))) as i32;
		self.fConst20 = F32::floor(0.153129 * self.fConst0 + 0.5);
		self.fConst21 = 6.9077554 * (self.fConst20 / self.fConst0);
		self.fConst22 = F32::floor(0.125 * self.fConst0 + 0.5);
		self.fConst23 = 6.9077554 * (self.fConst22 / self.fConst0);
		self.fConst24 = F32::floor(0.174713 * self.fConst0 + 0.5);
		self.fConst25 = 6.9077554 * (self.fConst24 / self.fConst0);
		self.fConst26 = F32::floor(0.219991 * self.fConst0 + 0.5);
		self.fConst27 = 6.9077554 * (self.fConst26 / self.fConst0);
		self.fConst28 = F32::floor(0.020346 * self.fConst0 + 0.5);
		self.iConst29 = (F32::min(32768.0, F32::max(0.0, self.fConst20 - self.fConst28))) as i32;
		self.iConst30 = (F32::min(4096.0, F32::max(0.0, self.fConst28 + -1.0))) as i32;
		self.fConst31 = F32::floor(0.019123 * self.fConst0 + 0.5);
		self.iConst32 = (F32::min(65536.0, F32::max(0.0, self.fConst26 - self.fConst31))) as i32;
		self.iConst33 = (F32::min(4096.0, F32::max(0.0, self.fConst31 + -1.0))) as i32;
		self.fConst34 = F32::floor(0.013458 * self.fConst0 + 0.5);
		self.iConst35 = (F32::min(32768.0, F32::max(0.0, self.fConst22 - self.fConst34))) as i32;
		self.iConst36 = (F32::min(4096.0, F32::max(0.0, self.fConst34 + -1.0))) as i32;
		self.fConst37 = F32::floor(0.192303 * self.fConst0 + 0.5);
		self.fConst38 = 6.9077554 * (self.fConst37 / self.fConst0);
		self.fConst39 = F32::floor(0.029291 * self.fConst0 + 0.5);
		self.iConst40 = (F32::min(32768.0, F32::max(0.0, self.fConst37 - self.fConst39))) as i32;
		self.fConst41 = F32::floor(0.022904 * self.fConst0 + 0.5);
		self.iConst42 = (F32::min(32768.0, F32::max(0.0, self.fConst24 - self.fConst41))) as i32;
		self.iConst43 = (F32::min(8192.0, F32::max(0.0, self.fConst41 + -1.0))) as i32;
		self.iConst44 = (F32::min(8192.0, F32::max(0.0, self.fConst39 + -1.0))) as i32;
	}
	pub fn instance_init(&mut self, sample_rate: i32) {
		self.instance_constants(sample_rate);
		self.instance_reset_params();
		self.instance_clear();
	}
	pub fn init(&mut self, sample_rate: i32) {
		Shimmer::class_init(sample_rate);
		self.instance_init(sample_rate);
	}
	
	pub fn build_user_interface(&self, ui_interface: &mut dyn UI<FaustFloat>) {
		Self::build_user_interface_static(ui_interface);
	}
	
	pub fn build_user_interface_static(ui_interface: &mut dyn UI<FaustFloat>) {
		ui_interface.open_vertical_box("shimmer");
		ui_interface.add_horizontal_slider("damping", ParamIndex(0), 8e+03, 5e+02, 2e+04, 1.0);
		ui_interface.add_horizontal_slider("decay", ParamIndex(1), 4.0, 0.1, 3e+01, 0.01);
		ui_interface.add_horizontal_slider("feedback", ParamIndex(2), 0.5, 0.0, 0.9, 0.001);
		ui_interface.add_horizontal_slider("shift", ParamIndex(3), 12.0, -12.0, 12.0, 0.01);
		ui_interface.close_box();
	}
	
	pub fn get_param(&self, param: ParamIndex) -> Option<FaustFloat> {
		match param.0 {
			1 => Some(self.fHslider0),
			0 => Some(self.fHslider1),
			3 => Some(self.fHslider2),
			2 => Some(self.fHslider3),
			_ => None,
		}
	}
	
	pub fn set_param(&mut self, param: ParamIndex, value: FaustFloat) {
		match param.0 {
			1 => { self.fHslider0 = value }
			0 => { self.fHslider1 = value }
			3 => { self.fHslider2 = value }
			2 => { self.fHslider3 = value }
			_ => {}
		}
	}
	
	pub fn compute(
		&mut self,
		count: usize,
		inputs: &[impl AsRef<[FaustFloat]>],
		outputs: &mut[impl AsMut<[FaustFloat]>],
	) {
		
		// Obtaining locks on 0 static var(s)
		let [inputs0, inputs1, .. ] = inputs.as_ref() else { panic!("wrong number of input buffers"); };
		let inputs0 = inputs0.as_ref()[..count].iter();
		let inputs1 = inputs1.as_ref()[..count].iter();
		let [outputs0, outputs1, .. ] = outputs.as_mut() else { panic!("wrong number of output buffers"); };
		let outputs0 = outputs0.as_mut()[..count].iter_mut();
		let outputs1 = outputs1.as_mut()[..count].iter_mut();
		let mut fSlow0: F32 = (self.fHslider0) as F32;
		let mut fSlow1: F32 = F32::exp(-(self.fConst5 / fSlow0));
		let mut fSlow2: F32 = Shimmer_faustpower2_f(fSlow1);
		let mut fSlow3: F32 = 1.0 - fSlow2;
		let mut fSlow4: F32 = F32::cos(self.fConst6 * (self.fHslider1) as F32);
		let mut fSlow5: F32 = 1.0 - fSlow2 * fSlow4;
		let mut fSlow6: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow5) / Shimmer_faustpower2_f(fSlow3) + -1.0));
		let mut fSlow7: F32 = fSlow5 / fSlow3;
		let mut fSlow8: F32 = fSlow7 - fSlow6;
		let mut fSlow9: F32 = fSlow1 * (fSlow6 + (1.0 - fSlow7));
		let mut fSlow10: F32 = F32::exp(-(self.fConst8 / fSlow0));
		let mut fSlow11: F32 = Shimmer_faustpower2_f(fSlow10);
		let mut fSlow12: F32 = 1.0 - fSlow11;
		let mut fSlow13: F32 = 1.0 - fSlow4 * fSlow11;
		let mut fSlow14: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow13) / Shimmer_faustpower2_f(fSlow12) + -1.0));
		let mut fSlow15: F32 = fSlow13 / fSlow12;
		let mut fSlow16: F32 = fSlow15 - fSlow14;
		let mut fSlow17: F32 = fSlow10 * (fSlow14 + (1.0 - fSlow15));
		let mut fSlow18: F32 = 0.00048828125 * (1.0 - F32::powf(2.0, 0.083333336 * (self.fHslider2) as F32));
		let mut fSlow19: F32 = 0.25 * (self.fHslider3) as F32;
		let mut fSlow20: F32 = F32::exp(-(self.fConst13 / fSlow0));
		let mut fSlow21: F32 = Shimmer_faustpower2_f(fSlow20);
		let mut fSlow22: F32 = 1.0 - fSlow21;
		let mut fSlow23: F32 = 1.0 - fSlow4 * fSlow21;
		let mut fSlow24: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow23) / Shimmer_faustpower2_f(fSlow22) + -1.0));
		let mut fSlow25: F32 = fSlow23 / fSlow22;
		let mut fSlow26: F32 = fSlow25 - fSlow24;
		let mut fSlow27: F32 = fSlow20 * (fSlow24 + (1.0 - fSlow25));
		let mut fSlow28: F32 = F32::exp(-(self.fConst21 / fSlow0));
		let mut fSlow29: F32 = Shimmer_faustpower2_f(fSlow28);
		let mut fSlow30: F32 = 1.0 - fSlow29;
		let mut fSlow31: F32 = 1.0 - fSlow4 * fSlow29;
		let mut fSlow32: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow31) / Shimmer_faustpower2_f(fSlow30) + -1.0));
		let mut fSlow33: F32 = fSlow31 / fSlow30;
		let mut fSlow34: F32 = fSlow33 - fSlow32;
		let mut fSlow35: F32 = fSlow28 * (fSlow32 + (1.0 - fSlow33));
		let mut fSlow36: F32 = F32::exp(-(self.fConst23 / fSlow0));
		let mut fSlow37: F32 = Shimmer_faustpower2_f(fSlow36);
		let mut fSlow38: F32 = 1.0 - fSlow37;
		let mut fSlow39: F32 = 1.0 - fSlow4 * fSlow37;
		let mut fSlow40: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow39) / Shimmer_faustpower2_f(fSlow38) + -1.0));
		let mut fSlow41: F32 = fSlow39 / fSlow38;
		let mut fSlow42: F32 = fSlow41 - fSlow40;
		let mut fSlow43: F32 = fSlow36 * (fSlow40 + (1.0 - fSlow41));
		let mut fSlow44: F32 = F32::exp(-(self.fConst25 / fSlow0));
		let mut fSlow45: F32 = Shimmer_faustpower2_f(fSlow44);
		let mut fSlow46: F32 = 1.0 - fSlow45;
		let mut fSlow47: F32 = 1.0 - fSlow4 * fSlow45;
		let mut fSlow48: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow47) / Shimmer_faustpower2_f(fSlow46) + -1.0));
		let mut fSlow49: F32 = fSlow47 / fSlow46;
		let mut fSlow50: F32 = fSlow49 - fSlow48;
		let mut fSlow51: F32 = fSlow44 * (fSlow48 + (1.0 - fSlow49));
		let mut fSlow52: F32 = F32::exp(-(self.fConst27 / fSlow0));
		let mut fSlow53: F32 = Shimmer_faustpower2_f(fSlow52);
		let mut fSlow54: F32 = 1.0 - fSlow53;
		let mut fSlow55: F32 = 1.0 - fSlow4 * fSlow53;
		let mut fSlow56: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow55) / Shimmer_faustpower2_f(fSlow54) + -1.0));
		let mut fSlow57: F32 = fSlow55 / fSlow54;
		let mut fSlow58: F32 = fSlow57 - fSlow56;
		let mut fSlow59: F32 = fSlow52 * (fSlow56 + (1.0 - fSlow57));
		let mut fSlow60: F32 = F32::exp(-(self.fConst38 / fSlow0));
		let mut fSlow61: F32 = Shimmer_faustpower2_f(fSlow60);
		let mut fSlow62: F32 = 1.0 - fSlow61;
		let mut fSlow63: F32 = 1.0 - fSlow4 * fSlow61;
		let mut fSlow64: F32 = F32::sqrt(F32::max(0.0, Shimmer_faustpower2_f(fSlow63) / Shimmer_faustpower2_f(fSlow62) + -1.0));
		let mut fSlow65: F32 = fSlow63 / fSlow62;
		let mut fSlow66: F32 = fSlow65 - fSlow64;
		let mut fSlow67: F32 = fSlow60 * (fSlow64 + (1.0 - fSlow65));
		let zipped_iterators = inputs0.zip(inputs1).zip(outputs0).zip(outputs1);
		for (((input0, input1), output0), output1) in zipped_iterators {
			let mut fTemp0: F32 = self.fConst3 * (self.fConst2 * self.fRec0[1] - (self.fRec38[1] + self.fRec38[2]));
			self.fRec0[0] = (if (-fTemp0.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp0} else {0.0});
			let mut fTemp1: F32 = self.fConst3 * (self.fConst2 * self.fRec1[1] - (self.fRec36[1] + self.fRec36[2]));
			self.fRec1[0] = (if (-fTemp1.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp1} else {0.0});
			let mut fTemp2: F32 = self.fConst3 * (self.fConst2 * self.fRec2[1] - (self.fRec33[1] + self.fRec33[2]));
			self.fRec2[0] = (if (-fTemp2.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp2} else {0.0});
			let mut fTemp3: F32 = fSlow9 * self.fRec38[1] + fSlow8 * self.fRec3[1];
			self.fRec3[0] = (if (fTemp3.to_bits().cast_signed()) & 2139095040 != 0 {fTemp3} else {0.0});
			let mut fTemp4: F32 = self.fConst3 * (self.fConst2 * self.fRec4[1] - (self.fRec34[1] + self.fRec34[2]));
			self.fRec4[0] = (if (-fTemp4.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp4} else {0.0});
			let mut fTemp5: F32 = self.fConst3 * (self.fConst2 * self.fRec5[1] - (self.fRec35[1] + self.fRec35[2]));
			self.fRec5[0] = (if (-fTemp5.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp5} else {0.0});
			let mut fTemp6: F32 = self.fConst3 * (self.fConst2 * self.fRec6[1] - (self.fRec39[1] + self.fRec39[2]));
			self.fRec6[0] = (if (-fTemp6.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp6} else {0.0});
			let mut fTemp7: F32 = fSlow17 * self.fRec39[1] + fSlow16 * self.fRec7[1];
			self.fRec7[0] = (if (fTemp7.to_bits().cast_signed()) & 2139095040 != 0 {fTemp7} else {0.0});
			let mut fTemp8: F32 = fSlow18 - F32::floor(self.fRec42[1]) + self.fRec42[1];
			self.fRec42[0] = (if (fTemp8.to_bits().cast_signed()) & 2139095040 != 0 {fTemp8} else {0.0});
			let mut fTemp9: F32 = 0.75 - F32::floor(0.75 + self.fRec42[0]) + self.fRec42[0];
			let mut fTemp10: F32 = (((fTemp9 >= 0.0) as i32) & ((fTemp9 <= 1.0) as i32)) as F32;
			let mut fTemp11: F32 = 1.0 - F32::cos(6.2831855 * fTemp9);
			let mut fTemp12: F32 = 2048.0 * fTemp9;
			let mut iTemp13: i32 = (fTemp12) as i32;
			let mut iTemp14: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, i32::wrapping_add(1, iTemp13))));
			let mut fTemp15: F32 = F32::floor(fTemp12);
			let mut fTemp16: F32 = fTemp12 - fTemp15;
			let mut fTemp17: F32 = 1.0 - fTemp12 + fTemp15;
			let mut iTemp18: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, iTemp13)));
			let mut fTemp19: F32 = 0.5 - F32::floor(0.5 + self.fRec42[0]) + self.fRec42[0];
			let mut fTemp20: F32 = (((fTemp19 >= 0.0) as i32) & ((fTemp19 <= 1.0) as i32)) as F32;
			let mut fTemp21: F32 = 1.0 - F32::cos(6.2831855 * fTemp19);
			let mut fTemp22: F32 = 2048.0 * fTemp19;
			let mut iTemp23: i32 = (fTemp22) as i32;
			let mut iTemp24: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, i32::wrapping_add(1, iTemp23))));
			let mut fTemp25: F32 = F32::floor(fTemp22);
			let mut fTemp26: F32 = fTemp22 - fTemp25;
			let mut fTemp27: F32 = 1.0 - fTemp22 + fTemp25;
			let mut iTemp28: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, iTemp23)));
			let mut fTemp29: F32 = 0.25 - F32::floor(0.25 + self.fRec42[0]) + self.fRec42[0];
			let mut fTemp30: F32 = (((fTemp29 >= 0.0) as i32) & ((fTemp29 <= 1.0) as i32)) as F32;
			let mut fTemp31: F32 = 1.0 - F32::cos(6.2831855 * fTemp29);
			let mut fTemp32: F32 = 2048.0 * fTemp29;
			let mut iTemp33: i32 = (fTemp32) as i32;
			let mut iTemp34: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, i32::wrapping_add(1, iTemp33))));
			let mut fTemp35: F32 = F32::floor(fTemp32);
			let mut fTemp36: F32 = fTemp32 - fTemp35;
			let mut fTemp37: F32 = 1.0 - fTemp32 + fTemp35;
			let mut iTemp38: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, iTemp33)));
			let mut fTemp39: F32 = self.fRec42[0] - F32::floor(self.fRec42[0]);
			let mut fTemp40: F32 = (((fTemp39 >= 0.0) as i32) & ((fTemp39 <= 1.0) as i32)) as F32;
			let mut fTemp41: F32 = 1.0 - F32::cos(6.2831855 * fTemp39);
			let mut fTemp42: F32 = 2048.0 * fTemp39;
			let mut iTemp43: i32 = (fTemp42) as i32;
			let mut iTemp44: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, i32::wrapping_add(1, iTemp43))));
			let mut fTemp45: F32 = F32::floor(fTemp42);
			let mut fTemp46: F32 = fTemp42 - fTemp45;
			let mut fTemp47: F32 = 1.0 - fTemp42 + fTemp45;
			let mut iTemp48: i32 = i32::wrapping_add(1, core::cmp::min(65537, core::cmp::max(0, iTemp43)));
			let mut fTemp49: F32 = 0.3 * ((*input0) as F32 + F32::tanh(fSlow19 * ((self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp48)) & 131071) as usize] * fTemp47 + fTemp46 * self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp44)) & 131071) as usize]) * fTemp41 * fTemp40 + (self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp38)) & 131071) as usize] * fTemp37 + fTemp36 * self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp34)) & 131071) as usize]) * fTemp31 * fTemp30 + (self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp28)) & 131071) as usize] * fTemp27 + fTemp26 * self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp24)) & 131071) as usize]) * fTemp21 * fTemp20 + (self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp18)) & 131071) as usize] * fTemp17 + fTemp16 * self.fRec41[((i32::wrapping_sub(self.IOTA0, iTemp14)) & 131071) as usize]) * fTemp11 * fTemp10)));
			self.fVec0[(self.IOTA0 & 32767) as usize] = 1e-20 + 0.35355338 * self.fRec3[0];
			let mut fTemp50: F32 = self.fVec0[((i32::wrapping_sub(self.IOTA0, self.iConst10)) & 32767) as usize] - (0.6 * self.fRec8[1] + fTemp49);
			self.fVec1[(self.IOTA0 & 8191) as usize] = fTemp50;
			let mut fTemp51: F32 = self.fVec1[((i32::wrapping_sub(self.IOTA0, self.iConst11)) & 8191) as usize];
			self.fRec8[0] = (if (fTemp51.to_bits().cast_signed()) & 2139095040 != 0 {fTemp51} else {0.0});
			let mut fTemp52: F32 = self.fConst3 * (self.fConst2 * self.fRec9[1] - (self.fRec32[1] + self.fRec32[2]));
			self.fRec9[0] = (if (-fTemp52.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp52} else {0.0});
			let mut fTemp53: F32 = fSlow27 * self.fRec36[1] + fSlow26 * self.fRec10[1];
			self.fRec10[0] = (if (fTemp53.to_bits().cast_signed()) & 2139095040 != 0 {fTemp53} else {0.0});
			let mut fTemp54: F32 = 0.6 * fTemp50;
			let mut fRec11: F32 = (if (fTemp54.to_bits().cast_signed()) & 2139095040 != 0 {fTemp54} else {0.0});
			let mut fTemp55: F32 = self.fConst3 * (self.fConst2 * self.fRec12[1] - (self.fRec37[1] + self.fRec37[2]));
			self.fRec12[0] = (if (-fTemp55.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp55} else {0.0});
			let mut fTemp56: F32 = 0.3 * ((*input1) as F32 + F32::tanh(fSlow19 * (fTemp41 * fTemp40 * (fTemp47 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp48)) & 131071) as usize] + fTemp46 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp44)) & 131071) as usize]) + fTemp31 * fTemp30 * (fTemp37 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp38)) & 131071) as usize] + fTemp36 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp34)) & 131071) as usize]) + fTemp21 * fTemp20 * (fTemp27 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp28)) & 131071) as usize] + fTemp26 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp24)) & 131071) as usize]) + fTemp11 * fTemp10 * (fTemp17 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp18)) & 131071) as usize] + fTemp16 * self.fRec40[((i32::wrapping_sub(self.IOTA0, iTemp14)) & 131071) as usize]))));
			self.fVec2[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec7[0];
			let mut fTemp57: F32 = 0.6 * self.fRec13[1] + self.fVec2[((i32::wrapping_sub(self.IOTA0, self.iConst15)) & 65535) as usize];
			self.fVec3[(self.IOTA0 & 8191) as usize] = fTemp57 - fTemp56;
			let mut fTemp58: F32 = self.fVec3[((i32::wrapping_sub(self.IOTA0, self.iConst16)) & 8191) as usize];
			self.fRec13[0] = (if (fTemp58.to_bits().cast_signed()) & 2139095040 != 0 {fTemp58} else {0.0});
			let mut fTemp59: F32 = 0.6 * (fTemp56 - fTemp57);
			let mut fRec14: F32 = (if (fTemp59.to_bits().cast_signed()) & 2139095040 != 0 {fTemp59} else {0.0});
			self.fVec4[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec10[0];
			let mut fTemp60: F32 = self.fVec4[((i32::wrapping_sub(self.IOTA0, self.iConst18)) & 65535) as usize] + fTemp56 + 0.6 * self.fRec15[1];
			self.fVec5[(self.IOTA0 & 8191) as usize] = fTemp60;
			let mut fTemp61: F32 = self.fVec5[((i32::wrapping_sub(self.IOTA0, self.iConst19)) & 8191) as usize];
			self.fRec15[0] = (if (fTemp61.to_bits().cast_signed()) & 2139095040 != 0 {fTemp61} else {0.0});
			let mut fTemp62: F32 = fSlow35 * self.fRec37[1] + fSlow34 * self.fRec16[1];
			self.fRec16[0] = (if (fTemp62.to_bits().cast_signed()) & 2139095040 != 0 {fTemp62} else {0.0});
			let mut fTemp63: F32 = fSlow43 * self.fRec35[1] + fSlow42 * self.fRec17[1];
			self.fRec17[0] = (if (fTemp63.to_bits().cast_signed()) & 2139095040 != 0 {fTemp63} else {0.0});
			let mut fTemp64: F32 = fSlow51 * self.fRec33[1] + fSlow50 * self.fRec18[1];
			self.fRec18[0] = (if (fTemp64.to_bits().cast_signed()) & 2139095040 != 0 {fTemp64} else {0.0});
			let mut fTemp65: F32 = fSlow59 * self.fRec34[1] + fSlow58 * self.fRec19[1];
			self.fRec19[0] = (if (fTemp65.to_bits().cast_signed()) & 2139095040 != 0 {fTemp65} else {0.0});
			self.fVec6[(self.IOTA0 & 32767) as usize] = 1e-20 + 0.35355338 * self.fRec16[0];
			let mut fTemp66: F32 = fTemp49 + self.fVec6[((i32::wrapping_sub(self.IOTA0, self.iConst29)) & 32767) as usize] - 0.6 * self.fRec20[1];
			self.fVec7[(self.IOTA0 & 4095) as usize] = fTemp66;
			let mut fTemp67: F32 = self.fVec7[((i32::wrapping_sub(self.IOTA0, self.iConst30)) & 4095) as usize];
			self.fRec20[0] = (if (fTemp67.to_bits().cast_signed()) & 2139095040 != 0 {fTemp67} else {0.0});
			self.fVec8[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec19[0];
			let mut fTemp68: F32 = 0.6 * self.fRec21[1] + self.fVec8[((i32::wrapping_sub(self.IOTA0, self.iConst32)) & 65535) as usize];
			self.fVec9[(self.IOTA0 & 4095) as usize] = fTemp68 - fTemp56;
			let mut fTemp69: F32 = self.fVec9[((i32::wrapping_sub(self.IOTA0, self.iConst33)) & 4095) as usize];
			self.fRec21[0] = (if (fTemp69.to_bits().cast_signed()) & 2139095040 != 0 {fTemp69} else {0.0});
			self.fVec10[(self.IOTA0 & 32767) as usize] = 1e-20 + 0.35355338 * self.fRec17[0];
			let mut fTemp70: F32 = self.fVec10[((i32::wrapping_sub(self.IOTA0, self.iConst35)) & 32767) as usize] - (fTemp49 + 0.6 * self.fRec23[1]);
			self.fVec11[(self.IOTA0 & 4095) as usize] = fTemp70;
			let mut fTemp71: F32 = 0.6 * fTemp70;
			let mut fRec22: F32 = (if (fTemp71.to_bits().cast_signed()) & 2139095040 != 0 {fTemp71} else {0.0});
			let mut fTemp72: F32 = self.fVec11[((i32::wrapping_sub(self.IOTA0, self.iConst36)) & 4095) as usize];
			self.fRec23[0] = (if (fTemp72.to_bits().cast_signed()) & 2139095040 != 0 {fTemp72} else {0.0});
			let mut fTemp73: F32 = 0.6 * fTemp66;
			let mut fRec24: F32 = (if (fTemp73.to_bits().cast_signed()) & 2139095040 != 0 {fTemp73} else {0.0});
			let mut fTemp74: F32 = 0.6 * (fTemp56 - fTemp68);
			let mut fRec25: F32 = (if (fTemp74.to_bits().cast_signed()) & 2139095040 != 0 {fTemp74} else {0.0});
			let mut fTemp75: F32 = fSlow67 * self.fRec32[1] + fSlow66 * self.fRec26[1];
			self.fRec26[0] = (if (fTemp75.to_bits().cast_signed()) & 2139095040 != 0 {fTemp75} else {0.0});
			let mut fTemp76: F32 = 0.6 * fTemp60;
			let mut fRec27: F32 = (if (-fTemp76.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp76} else {0.0});
			self.fVec12[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec26[0];
			let mut fTemp77: F32 = self.fVec12[((i32::wrapping_sub(self.IOTA0, self.iConst40)) & 65535) as usize] + fTemp56 + 0.6 * self.fRec31[1];
			self.fVec13[(self.IOTA0 & 8191) as usize] = fTemp77;
			let mut fTemp78: F32 = 0.6 * fTemp77;
			let mut fRec28: F32 = (if (-fTemp78.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp78} else {0.0});
			self.fVec14[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec18[0];
			let mut fTemp79: F32 = fTemp49 + self.fVec14[((i32::wrapping_sub(self.IOTA0, self.iConst42)) & 65535) as usize] - 0.6 * self.fRec29[1];
			self.fVec15[(self.IOTA0 & 8191) as usize] = fTemp79;
			let mut fTemp80: F32 = self.fVec15[((i32::wrapping_sub(self.IOTA0, self.iConst43)) & 8191) as usize];
			self.fRec29[0] = (if (fTemp80.to_bits().cast_signed()) & 2139095040 != 0 {fTemp80} else {0.0});
			let mut fTemp81: F32 = 0.6 * fTemp79;
			let mut fRec30: F32 = (if (fTemp81.to_bits().cast_signed()) & 2139095040 != 0 {fTemp81} else {0.0});
			let mut fTemp82: F32 = self.fVec13[((i32::wrapping_sub(self.IOTA0, self.iConst44)) & 8191) as usize];
			self.fRec31[0] = (if (fTemp82.to_bits().cast_signed()) & 2139095040 != 0 {fTemp82} else {0.0});
			let mut fTemp83: F32 = self.fRec13[1] + self.fRec15[1];
			let mut fTemp84: F32 = self.fRec8[1] + self.fRec20[1];
			let mut fTemp85: F32 = fRec25 + fRec28 + fRec11 + fRec24 + self.fRec31[1] + self.fRec21[1] + fTemp84 - (fRec14 + fRec27 + fRec22 + fRec30 + self.fRec29[1] + self.fRec23[1] + fTemp83);
			self.fRec32[0] = (if (fTemp85.to_bits().cast_signed()) & 2139095040 != 0 {fTemp85} else {0.0});
			let mut fTemp86: F32 = self.fRec8[1] + self.fRec13[1];
			let mut fTemp87: F32 = self.fRec20[1] + self.fRec15[1] + fTemp86;
			let mut fTemp88: F32 = fRec14 + fRec27 + fRec11 + fRec24 + fTemp87 - (fRec25 + fRec28 + fRec22 + fRec30 + self.fRec29[1] + self.fRec31[1] + self.fRec21[1] + self.fRec23[1]);
			self.fRec33[0] = (if (fTemp88.to_bits().cast_signed()) & 2139095040 != 0 {fTemp88} else {0.0});
			let mut fTemp89: F32 = self.fRec8[1] + self.fRec15[1];
			let mut fTemp90: F32 = self.fRec13[1] + self.fRec20[1];
			let mut fTemp91: F32 = fRec14 + fRec28 + fRec22 + fRec24 + self.fRec31[1] + self.fRec23[1] + fTemp90 - (fRec25 + fRec27 + fRec11 + fRec30 + self.fRec29[1] + self.fRec21[1] + fTemp89);
			self.fRec34[0] = (if (fTemp91.to_bits().cast_signed()) & 2139095040 != 0 {fTemp91} else {0.0});
			let mut fTemp92: F32 = self.fRec15[1] + self.fRec20[1];
			let mut fTemp93: F32 = fRec25 + fRec27 + fRec22 + fRec24 + self.fRec23[1] + self.fRec21[1] + fTemp92 - (fRec14 + fRec28 + fRec11 + fRec30 + self.fRec29[1] + self.fRec31[1] + fTemp86);
			self.fRec35[0] = (if (fTemp93.to_bits().cast_signed()) & 2139095040 != 0 {fTemp93} else {0.0});
			let mut fTemp94: F32 = fRec22 + fRec11 + fRec30 + fRec24 + self.fRec29[1] + self.fRec23[1] + fTemp84 - (fRec25 + fRec14 + fRec28 + fRec27 + self.fRec31[1] + self.fRec21[1] + fTemp83);
			self.fRec36[0] = (if (fTemp94.to_bits().cast_signed()) & 2139095040 != 0 {fTemp94} else {0.0});
			let mut fTemp95: F32 = fRec25 + fRec14 + fRec28 + fRec27 + fRec22 + fRec11 + fRec30 + fRec24 + self.fRec29[1] + self.fRec31[1] + self.fRec23[1] + self.fRec21[1] + fTemp87;
			self.fRec37[0] = (if (fTemp95.to_bits().cast_signed()) & 2139095040 != 0 {fTemp95} else {0.0});
			let mut fTemp96: F32 = fRec28 + fRec27 + fRec30 + fRec24 + self.fRec29[1] + self.fRec31[1] + fTemp92 - (fRec25 + fRec14 + fRec22 + fRec11 + self.fRec23[1] + self.fRec21[1] + fTemp86);
			self.fRec38[0] = (if (fTemp96.to_bits().cast_signed()) & 2139095040 != 0 {fTemp96} else {0.0});
			let mut fTemp97: F32 = fRec25 + fRec14 + fRec30 + fRec24 + self.fRec29[1] + self.fRec21[1] + fTemp90 - (fRec28 + fRec27 + fRec22 + fRec11 + self.fRec31[1] + self.fRec23[1] + fTemp89);
			self.fRec39[0] = (if (fTemp97.to_bits().cast_signed()) & 2139095040 != 0 {fTemp97} else {0.0});
			let mut fTemp98: F32 = 0.37 * (self.fRec36[0] - self.fRec38[0]);
			self.fRec40[(self.IOTA0 & 131071) as usize] = (if (fTemp98.to_bits().cast_signed()) & 2139095040 != 0 {fTemp98} else {0.0});
			let mut fTemp99: F32 = 0.37 * (self.fRec36[0] + self.fRec38[0]);
			self.fRec41[(self.IOTA0 & 131071) as usize] = (if (fTemp99.to_bits().cast_signed()) & 2139095040 != 0 {fTemp99} else {0.0});
			*output0 = (self.fRec41[(self.IOTA0 & 131071) as usize]) as FaustFloat;
			*output1 = (self.fRec40[(self.IOTA0 & 131071) as usize]) as FaustFloat;
			self.fRec0[1] = self.fRec0[0];
			self.fRec1[1] = self.fRec1[0];
			self.fRec2[1] = self.fRec2[0];
			self.fRec3[1] = self.fRec3[0];
			self.fRec4[1] = self.fRec4[0];
			self.fRec5[1] = self.fRec5[0];
			self.fRec6[1] = self.fRec6[0];
			self.fRec7[1] = self.fRec7[0];
			self.fRec42[1] = self.fRec42[0];
			self.IOTA0 = i32::wrapping_add(self.IOTA0, 1);
			self.fRec8[1] = self.fRec8[0];
			self.fRec9[1] = self.fRec9[0];
			self.fRec10[1] = self.fRec10[0];
			self.fRec12[1] = self.fRec12[0];
			self.fRec13[1] = self.fRec13[0];
			self.fRec15[1] = self.fRec15[0];
			self.fRec16[1] = self.fRec16[0];
			self.fRec17[1] = self.fRec17[0];
			self.fRec18[1] = self.fRec18[0];
			self.fRec19[1] = self.fRec19[0];
			self.fRec20[1] = self.fRec20[0];
			self.fRec21[1] = self.fRec21[0];
			self.fRec23[1] = self.fRec23[0];
			self.fRec26[1] = self.fRec26[0];
			self.fRec29[1] = self.fRec29[0];
			self.fRec31[1] = self.fRec31[0];
			self.fRec32[2] = self.fRec32[1];
			self.fRec32[1] = self.fRec32[0];
			self.fRec33[2] = self.fRec33[1];
			self.fRec33[1] = self.fRec33[0];
			self.fRec34[2] = self.fRec34[1];
			self.fRec34[1] = self.fRec34[0];
			self.fRec35[2] = self.fRec35[1];
			self.fRec35[1] = self.fRec35[0];
			self.fRec36[2] = self.fRec36[1];
			self.fRec36[1] = self.fRec36[0];
			self.fRec37[2] = self.fRec37[1];
			self.fRec37[1] = self.fRec37[0];
			self.fRec38[2] = self.fRec38[1];
			self.fRec38[1] = self.fRec38[0];
			self.fRec39[2] = self.fRec39[1];
			self.fRec39[1] = self.fRec39[0];
		}
		
	}

}

