// Generated from core/faust/room.dsp by core/faust/generate.sh; do not edit.
/* ------------------------------------------------------------
name: "room"
Code generated with Faust 2.88.0 (https://faust.grame.fr)
Compilation options: -lang rust -fpga-mem-th 4 -light -ct 1 -cn Room -es 1 -mcd 16 -mdd 1024 -mdy 33 -single -ftz 2
------------------------------------------------------------ */

#[repr(C)]
pub struct Room {
	fSampleRate: i32,
	fConst0: F32,
	fConst1: F32,
	fConst2: F32,
	fConst3: F32,
	fRec0: [F32;2],
	fRec1: [F32;2],
	fRec2: [F32;2],
	fRec3: [F32;2],
	fHslider0: FaustFloat,
	fConst4: F32,
	fConst5: F32,
	fHslider1: FaustFloat,
	fConst6: F32,
	fRec4: [F32;2],
	fConst7: F32,
	fConst8: F32,
	fRec5: [F32;2],
	IOTA0: i32,
	fVec0: [F32;65536],
	fConst9: F32,
	iConst10: i32,
	fVec1: [F32;8192],
	iConst11: i32,
	fRec6: [F32;2],
	fVec2: [F32;32768],
	fConst12: F32,
	iConst13: i32,
	fVec3: [F32;4096],
	fRec8: [F32;2],
	fConst14: F32,
	fConst15: F32,
	fRec9: [F32;2],
	fRec10: [F32;2],
	fVec4: [F32;65536],
	fConst16: F32,
	iConst17: i32,
	fVec5: [F32;8192],
	iConst18: i32,
	fRec11: [F32;2],
	fRec13: [F32;2],
	fRec14: [F32;2],
	fConst19: F32,
	fConst20: F32,
	fRec15: [F32;2],
	fVec6: [F32;65536],
	fConst21: F32,
	iConst22: i32,
	fVec7: [F32;4096],
	iConst23: i32,
	fRec16: [F32;2],
	fConst24: F32,
	fConst25: F32,
	fRec17: [F32;2],
	fConst26: F32,
	fConst27: F32,
	fRec18: [F32;2],
	fConst28: F32,
	fConst29: F32,
	fRec19: [F32;2],
	iConst30: i32,
	fRec20: [F32;2],
	fVec8: [F32;65536],
	fConst31: F32,
	iConst32: i32,
	fVec9: [F32;8192],
	fVec10: [F32;65536],
	fConst33: F32,
	iConst34: i32,
	fVec11: [F32;8192],
	fVec12: [F32;32768],
	fConst35: F32,
	iConst36: i32,
	fVec13: [F32;4096],
	iConst37: i32,
	fRec25: [F32;2],
	iConst38: i32,
	fRec26: [F32;2],
	fConst39: F32,
	fConst40: F32,
	fRec28: [F32;2],
	iConst41: i32,
	fRec29: [F32;2],
	fVec14: [F32;32768],
	fConst42: F32,
	iConst43: i32,
	fVec15: [F32;8192],
	iConst44: i32,
	fRec30: [F32;2],
	fRec32: [F32;3],
	fRec33: [F32;3],
	fRec34: [F32;3],
	fRec35: [F32;3],
	fRec36: [F32;3],
	fRec37: [F32;3],
	fRec38: [F32;3],
	fRec39: [F32;3],
}


fn Room_faustpower2_f(value: F32) -> F32 {
	return value * value;
}
pub const FAUST_INPUTS: usize = 2;
pub const FAUST_OUTPUTS: usize = 2;
pub const FAUST_ACTIVES: usize = 2;
pub const FAUST_PASSIVES: usize = 0;

impl Room {
		
	pub fn new() -> Room { 
		Room {
			fSampleRate: 0,
			fConst0: 0.0,
			fConst1: 0.0,
			fConst2: 0.0,
			fConst3: 0.0,
			fRec0: [0.0;2],
			fRec1: [0.0;2],
			fRec2: [0.0;2],
			fRec3: [0.0;2],
			fHslider0: 0.0,
			fConst4: 0.0,
			fConst5: 0.0,
			fHslider1: 0.0,
			fConst6: 0.0,
			fRec4: [0.0;2],
			fConst7: 0.0,
			fConst8: 0.0,
			fRec5: [0.0;2],
			IOTA0: 0,
			fVec0: [0.0;65536],
			fConst9: 0.0,
			iConst10: 0,
			fVec1: [0.0;8192],
			iConst11: 0,
			fRec6: [0.0;2],
			fVec2: [0.0;32768],
			fConst12: 0.0,
			iConst13: 0,
			fVec3: [0.0;4096],
			fRec8: [0.0;2],
			fConst14: 0.0,
			fConst15: 0.0,
			fRec9: [0.0;2],
			fRec10: [0.0;2],
			fVec4: [0.0;65536],
			fConst16: 0.0,
			iConst17: 0,
			fVec5: [0.0;8192],
			iConst18: 0,
			fRec11: [0.0;2],
			fRec13: [0.0;2],
			fRec14: [0.0;2],
			fConst19: 0.0,
			fConst20: 0.0,
			fRec15: [0.0;2],
			fVec6: [0.0;65536],
			fConst21: 0.0,
			iConst22: 0,
			fVec7: [0.0;4096],
			iConst23: 0,
			fRec16: [0.0;2],
			fConst24: 0.0,
			fConst25: 0.0,
			fRec17: [0.0;2],
			fConst26: 0.0,
			fConst27: 0.0,
			fRec18: [0.0;2],
			fConst28: 0.0,
			fConst29: 0.0,
			fRec19: [0.0;2],
			iConst30: 0,
			fRec20: [0.0;2],
			fVec8: [0.0;65536],
			fConst31: 0.0,
			iConst32: 0,
			fVec9: [0.0;8192],
			fVec10: [0.0;65536],
			fConst33: 0.0,
			iConst34: 0,
			fVec11: [0.0;8192],
			fVec12: [0.0;32768],
			fConst35: 0.0,
			iConst36: 0,
			fVec13: [0.0;4096],
			iConst37: 0,
			fRec25: [0.0;2],
			iConst38: 0,
			fRec26: [0.0;2],
			fConst39: 0.0,
			fConst40: 0.0,
			fRec28: [0.0;2],
			iConst41: 0,
			fRec29: [0.0;2],
			fVec14: [0.0;32768],
			fConst42: 0.0,
			iConst43: 0,
			fVec15: [0.0;8192],
			iConst44: 0,
			fRec30: [0.0;2],
			fRec32: [0.0;3],
			fRec33: [0.0;3],
			fRec34: [0.0;3],
			fRec35: [0.0;3],
			fRec36: [0.0;3],
			fRec37: [0.0;3],
			fRec38: [0.0;3],
			fRec39: [0.0;3],
		}
	}
	pub fn metadata(&self, m: &mut dyn Meta) { 
		m.declare("basics.lib/name", r"Faust Basic Element Library");
		m.declare("basics.lib/version", r"1.23.0");
		m.declare("compile_options", r"-lang rust -fpga-mem-th 4 -light -ct 1 -cn Room -es 1 -mcd 16 -mdd 1024 -mdy 33 -single -ftz 2");
		m.declare("delays.lib/name", r"Faust Delay Library");
		m.declare("delays.lib/version", r"1.2.0");
		m.declare("filename", r"room.dsp");
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
		m.declare("name", r"room");
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
		self.fHslider0 = (0.8) as FaustFloat;
		self.fHslider1 = (6e+03) as FaustFloat;
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
		self.IOTA0 = 0;
		for l6 in 0..65536 {
			self.fVec0[l6 as usize] = 0.0;
		}
		for l7 in 0..8192 {
			self.fVec1[l7 as usize] = 0.0;
		}
		for l8 in 0..2 {
			self.fRec6[l8 as usize] = 0.0;
		}
		for l9 in 0..32768 {
			self.fVec2[l9 as usize] = 0.0;
		}
		for l10 in 0..4096 {
			self.fVec3[l10 as usize] = 0.0;
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
		for l14 in 0..65536 {
			self.fVec4[l14 as usize] = 0.0;
		}
		for l15 in 0..8192 {
			self.fVec5[l15 as usize] = 0.0;
		}
		for l16 in 0..2 {
			self.fRec11[l16 as usize] = 0.0;
		}
		for l17 in 0..2 {
			self.fRec13[l17 as usize] = 0.0;
		}
		for l18 in 0..2 {
			self.fRec14[l18 as usize] = 0.0;
		}
		for l19 in 0..2 {
			self.fRec15[l19 as usize] = 0.0;
		}
		for l20 in 0..65536 {
			self.fVec6[l20 as usize] = 0.0;
		}
		for l21 in 0..4096 {
			self.fVec7[l21 as usize] = 0.0;
		}
		for l22 in 0..2 {
			self.fRec16[l22 as usize] = 0.0;
		}
		for l23 in 0..2 {
			self.fRec17[l23 as usize] = 0.0;
		}
		for l24 in 0..2 {
			self.fRec18[l24 as usize] = 0.0;
		}
		for l25 in 0..2 {
			self.fRec19[l25 as usize] = 0.0;
		}
		for l26 in 0..2 {
			self.fRec20[l26 as usize] = 0.0;
		}
		for l27 in 0..65536 {
			self.fVec8[l27 as usize] = 0.0;
		}
		for l28 in 0..8192 {
			self.fVec9[l28 as usize] = 0.0;
		}
		for l29 in 0..65536 {
			self.fVec10[l29 as usize] = 0.0;
		}
		for l30 in 0..8192 {
			self.fVec11[l30 as usize] = 0.0;
		}
		for l31 in 0..32768 {
			self.fVec12[l31 as usize] = 0.0;
		}
		for l32 in 0..4096 {
			self.fVec13[l32 as usize] = 0.0;
		}
		for l33 in 0..2 {
			self.fRec25[l33 as usize] = 0.0;
		}
		for l34 in 0..2 {
			self.fRec26[l34 as usize] = 0.0;
		}
		for l35 in 0..2 {
			self.fRec28[l35 as usize] = 0.0;
		}
		for l36 in 0..2 {
			self.fRec29[l36 as usize] = 0.0;
		}
		for l37 in 0..32768 {
			self.fVec14[l37 as usize] = 0.0;
		}
		for l38 in 0..8192 {
			self.fVec15[l38 as usize] = 0.0;
		}
		for l39 in 0..2 {
			self.fRec30[l39 as usize] = 0.0;
		}
		for l40 in 0..3 {
			self.fRec32[l40 as usize] = 0.0;
		}
		for l41 in 0..3 {
			self.fRec33[l41 as usize] = 0.0;
		}
		for l42 in 0..3 {
			self.fRec34[l42 as usize] = 0.0;
		}
		for l43 in 0..3 {
			self.fRec35[l43 as usize] = 0.0;
		}
		for l44 in 0..3 {
			self.fRec36[l44 as usize] = 0.0;
		}
		for l45 in 0..3 {
			self.fRec37[l45 as usize] = 0.0;
		}
		for l46 in 0..3 {
			self.fRec38[l46 as usize] = 0.0;
		}
		for l47 in 0..3 {
			self.fRec39[l47 as usize] = 0.0;
		}
	}
	pub fn instance_constants(&mut self, sample_rate: i32) {
		// Obtaining locks on 0 static var(s)
		self.fSampleRate = sample_rate;
		self.fConst0 = F32::min(1.92e+05, F32::max(1.0, (self.fSampleRate) as F32));
		self.fConst1 = 1.0 / F32::tan(628.31854 / self.fConst0);
		self.fConst2 = 1.0 - self.fConst1;
		self.fConst3 = 1.0 / (self.fConst1 + 1.0);
		self.fConst4 = F32::floor(0.256891 * self.fConst0 + 0.5);
		self.fConst5 = 6.9077554 * (self.fConst4 / self.fConst0);
		self.fConst6 = 6.2831855 / self.fConst0;
		self.fConst7 = F32::floor(0.153129 * self.fConst0 + 0.5);
		self.fConst8 = 6.9077554 * (self.fConst7 / self.fConst0);
		self.fConst9 = F32::floor(0.027333 * self.fConst0 + 0.5);
		self.iConst10 = (F32::min(65536.0, F32::max(0.0, self.fConst4 - self.fConst9))) as i32;
		self.iConst11 = (F32::min(8192.0, F32::max(0.0, self.fConst9 + -1.0))) as i32;
		self.fConst12 = F32::floor(0.020346 * self.fConst0 + 0.5);
		self.iConst13 = (F32::min(32768.0, F32::max(0.0, self.fConst7 - self.fConst12))) as i32;
		self.fConst14 = F32::floor(0.192303 * self.fConst0 + 0.5);
		self.fConst15 = 6.9077554 * (self.fConst14 / self.fConst0);
		self.fConst16 = F32::floor(0.029291 * self.fConst0 + 0.5);
		self.iConst17 = (F32::min(32768.0, F32::max(0.0, self.fConst14 - self.fConst16))) as i32;
		self.iConst18 = (F32::min(8192.0, F32::max(0.0, self.fConst16 + -1.0))) as i32;
		self.fConst19 = F32::floor(0.219991 * self.fConst0 + 0.5);
		self.fConst20 = 6.9077554 * (self.fConst19 / self.fConst0);
		self.fConst21 = F32::floor(0.019123 * self.fConst0 + 0.5);
		self.iConst22 = (F32::min(65536.0, F32::max(0.0, self.fConst19 - self.fConst21))) as i32;
		self.iConst23 = (F32::min(4096.0, F32::max(0.0, self.fConst21 + -1.0))) as i32;
		self.fConst24 = F32::floor(0.210389 * self.fConst0 + 0.5);
		self.fConst25 = 6.9077554 * (self.fConst24 / self.fConst0);
		self.fConst26 = F32::floor(0.125 * self.fConst0 + 0.5);
		self.fConst27 = 6.9077554 * (self.fConst26 / self.fConst0);
		self.fConst28 = F32::floor(0.174713 * self.fConst0 + 0.5);
		self.fConst29 = 6.9077554 * (self.fConst28 / self.fConst0);
		self.iConst30 = (F32::min(4096.0, F32::max(0.0, self.fConst12 + -1.0))) as i32;
		self.fConst31 = F32::floor(0.022904 * self.fConst0 + 0.5);
		self.iConst32 = (F32::min(32768.0, F32::max(0.0, self.fConst28 - self.fConst31))) as i32;
		self.fConst33 = F32::floor(0.024421 * self.fConst0 + 0.5);
		self.iConst34 = (F32::min(65536.0, F32::max(0.0, self.fConst24 - self.fConst33))) as i32;
		self.fConst35 = F32::floor(0.013458 * self.fConst0 + 0.5);
		self.iConst36 = (F32::min(32768.0, F32::max(0.0, self.fConst26 - self.fConst35))) as i32;
		self.iConst37 = (F32::min(8192.0, F32::max(0.0, self.fConst31 + -1.0))) as i32;
		self.iConst38 = (F32::min(8192.0, F32::max(0.0, self.fConst33 + -1.0))) as i32;
		self.fConst39 = F32::floor(0.127837 * self.fConst0 + 0.5);
		self.fConst40 = 6.9077554 * (self.fConst39 / self.fConst0);
		self.iConst41 = (F32::min(4096.0, F32::max(0.0, self.fConst35 + -1.0))) as i32;
		self.fConst42 = F32::floor(0.031604 * self.fConst0 + 0.5);
		self.iConst43 = (F32::min(32768.0, F32::max(0.0, self.fConst39 - self.fConst42))) as i32;
		self.iConst44 = (F32::min(8192.0, F32::max(0.0, self.fConst42 + -1.0))) as i32;
	}
	pub fn instance_init(&mut self, sample_rate: i32) {
		self.instance_constants(sample_rate);
		self.instance_reset_params();
		self.instance_clear();
	}
	pub fn init(&mut self, sample_rate: i32) {
		Room::class_init(sample_rate);
		self.instance_init(sample_rate);
	}
	
	pub fn build_user_interface(&self, ui_interface: &mut dyn UI<FaustFloat>) {
		Self::build_user_interface_static(ui_interface);
	}
	
	pub fn build_user_interface_static(ui_interface: &mut dyn UI<FaustFloat>) {
		ui_interface.open_vertical_box("room");
		ui_interface.add_horizontal_slider("damping", ParamIndex(0), 6e+03, 5e+02, 2e+04, 1.0);
		ui_interface.add_horizontal_slider("decay", ParamIndex(1), 0.8, 0.1, 3e+01, 0.01);
		ui_interface.close_box();
	}
	
	pub fn get_param(&self, param: ParamIndex) -> Option<FaustFloat> {
		match param.0 {
			1 => Some(self.fHslider0),
			0 => Some(self.fHslider1),
			_ => None,
		}
	}
	
	pub fn set_param(&mut self, param: ParamIndex, value: FaustFloat) {
		match param.0 {
			1 => { self.fHslider0 = value }
			0 => { self.fHslider1 = value }
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
		let mut fSlow2: F32 = Room_faustpower2_f(fSlow1);
		let mut fSlow3: F32 = 1.0 - fSlow2;
		let mut fSlow4: F32 = F32::cos(self.fConst6 * (self.fHslider1) as F32);
		let mut fSlow5: F32 = 1.0 - fSlow2 * fSlow4;
		let mut fSlow6: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow5) / Room_faustpower2_f(fSlow3) + -1.0));
		let mut fSlow7: F32 = fSlow5 / fSlow3;
		let mut fSlow8: F32 = fSlow7 - fSlow6;
		let mut fSlow9: F32 = fSlow1 * (fSlow6 + (1.0 - fSlow7));
		let mut fSlow10: F32 = F32::exp(-(self.fConst8 / fSlow0));
		let mut fSlow11: F32 = Room_faustpower2_f(fSlow10);
		let mut fSlow12: F32 = 1.0 - fSlow11;
		let mut fSlow13: F32 = 1.0 - fSlow4 * fSlow11;
		let mut fSlow14: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow13) / Room_faustpower2_f(fSlow12) + -1.0));
		let mut fSlow15: F32 = fSlow13 / fSlow12;
		let mut fSlow16: F32 = fSlow15 - fSlow14;
		let mut fSlow17: F32 = fSlow10 * (fSlow14 + (1.0 - fSlow15));
		let mut fSlow18: F32 = F32::exp(-(self.fConst15 / fSlow0));
		let mut fSlow19: F32 = Room_faustpower2_f(fSlow18);
		let mut fSlow20: F32 = 1.0 - fSlow19;
		let mut fSlow21: F32 = 1.0 - fSlow4 * fSlow19;
		let mut fSlow22: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow21) / Room_faustpower2_f(fSlow20) + -1.0));
		let mut fSlow23: F32 = fSlow21 / fSlow20;
		let mut fSlow24: F32 = fSlow23 - fSlow22;
		let mut fSlow25: F32 = fSlow18 * (fSlow22 + (1.0 - fSlow23));
		let mut fSlow26: F32 = F32::exp(-(self.fConst20 / fSlow0));
		let mut fSlow27: F32 = Room_faustpower2_f(fSlow26);
		let mut fSlow28: F32 = 1.0 - fSlow27;
		let mut fSlow29: F32 = 1.0 - fSlow4 * fSlow27;
		let mut fSlow30: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow29) / Room_faustpower2_f(fSlow28) + -1.0));
		let mut fSlow31: F32 = fSlow29 / fSlow28;
		let mut fSlow32: F32 = fSlow31 - fSlow30;
		let mut fSlow33: F32 = fSlow26 * (fSlow30 + (1.0 - fSlow31));
		let mut fSlow34: F32 = F32::exp(-(self.fConst25 / fSlow0));
		let mut fSlow35: F32 = Room_faustpower2_f(fSlow34);
		let mut fSlow36: F32 = 1.0 - fSlow35;
		let mut fSlow37: F32 = 1.0 - fSlow4 * fSlow35;
		let mut fSlow38: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow37) / Room_faustpower2_f(fSlow36) + -1.0));
		let mut fSlow39: F32 = fSlow37 / fSlow36;
		let mut fSlow40: F32 = fSlow39 - fSlow38;
		let mut fSlow41: F32 = fSlow34 * (fSlow38 + (1.0 - fSlow39));
		let mut fSlow42: F32 = F32::exp(-(self.fConst27 / fSlow0));
		let mut fSlow43: F32 = Room_faustpower2_f(fSlow42);
		let mut fSlow44: F32 = 1.0 - fSlow43;
		let mut fSlow45: F32 = 1.0 - fSlow4 * fSlow43;
		let mut fSlow46: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow45) / Room_faustpower2_f(fSlow44) + -1.0));
		let mut fSlow47: F32 = fSlow45 / fSlow44;
		let mut fSlow48: F32 = fSlow47 - fSlow46;
		let mut fSlow49: F32 = fSlow42 * (fSlow46 + (1.0 - fSlow47));
		let mut fSlow50: F32 = F32::exp(-(self.fConst29 / fSlow0));
		let mut fSlow51: F32 = Room_faustpower2_f(fSlow50);
		let mut fSlow52: F32 = 1.0 - fSlow51;
		let mut fSlow53: F32 = 1.0 - fSlow4 * fSlow51;
		let mut fSlow54: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow53) / Room_faustpower2_f(fSlow52) + -1.0));
		let mut fSlow55: F32 = fSlow53 / fSlow52;
		let mut fSlow56: F32 = fSlow55 - fSlow54;
		let mut fSlow57: F32 = fSlow50 * (fSlow54 + (1.0 - fSlow55));
		let mut fSlow58: F32 = F32::exp(-(self.fConst40 / fSlow0));
		let mut fSlow59: F32 = Room_faustpower2_f(fSlow58);
		let mut fSlow60: F32 = 1.0 - fSlow59;
		let mut fSlow61: F32 = 1.0 - fSlow4 * fSlow59;
		let mut fSlow62: F32 = F32::sqrt(F32::max(0.0, Room_faustpower2_f(fSlow61) / Room_faustpower2_f(fSlow60) + -1.0));
		let mut fSlow63: F32 = fSlow61 / fSlow60;
		let mut fSlow64: F32 = fSlow63 - fSlow62;
		let mut fSlow65: F32 = fSlow58 * (fSlow62 + (1.0 - fSlow63));
		let zipped_iterators = inputs0.zip(inputs1).zip(outputs0).zip(outputs1);
		for (((input0, input1), output0), output1) in zipped_iterators {
			let mut fTemp0: F32 = self.fConst3 * (self.fConst2 * self.fRec0[1] - (self.fRec34[1] + self.fRec34[2]));
			self.fRec0[0] = (if (-fTemp0.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp0} else {0.0});
			let mut fTemp1: F32 = self.fConst3 * (self.fConst2 * self.fRec1[1] - (self.fRec37[1] + self.fRec37[2]));
			self.fRec1[0] = (if (-fTemp1.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp1} else {0.0});
			let mut fTemp2: F32 = self.fConst3 * (self.fConst2 * self.fRec2[1] - (self.fRec39[1] + self.fRec39[2]));
			self.fRec2[0] = (if (-fTemp2.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp2} else {0.0});
			let mut fTemp3: F32 = self.fConst3 * (self.fConst2 * self.fRec3[1] - (self.fRec36[1] + self.fRec36[2]));
			self.fRec3[0] = (if (-fTemp3.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp3} else {0.0});
			let mut fTemp4: F32 = fSlow9 * self.fRec39[1] + fSlow8 * self.fRec4[1];
			self.fRec4[0] = (if (fTemp4.to_bits().cast_signed()) & 2139095040 != 0 {fTemp4} else {0.0});
			let mut fTemp5: F32 = fSlow17 * self.fRec37[1] + fSlow16 * self.fRec5[1];
			self.fRec5[0] = (if (fTemp5.to_bits().cast_signed()) & 2139095040 != 0 {fTemp5} else {0.0});
			let mut fTemp6: F32 = 0.3 * (*input1) as F32;
			self.fVec0[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec4[0];
			let mut fTemp7: F32 = 0.6 * self.fRec6[1] + self.fVec0[((i32::wrapping_sub(self.IOTA0, self.iConst10)) & 65535) as usize];
			self.fVec1[(self.IOTA0 & 8191) as usize] = fTemp7 - fTemp6;
			let mut fTemp8: F32 = self.fVec1[((i32::wrapping_sub(self.IOTA0, self.iConst11)) & 8191) as usize];
			self.fRec6[0] = (if (fTemp8.to_bits().cast_signed()) & 2139095040 != 0 {fTemp8} else {0.0});
			self.fVec2[(self.IOTA0 & 32767) as usize] = 1e-20 + 0.35355338 * self.fRec5[0];
			let mut fTemp9: F32 = 0.3 * (*input0) as F32;
			let mut fTemp10: F32 = fTemp9 - 0.6 * self.fRec20[1] + self.fVec2[((i32::wrapping_sub(self.IOTA0, self.iConst13)) & 32767) as usize];
			self.fVec3[(self.IOTA0 & 4095) as usize] = fTemp10;
			let mut fTemp11: F32 = 0.6 * fTemp10;
			let mut fRec7: F32 = (if (fTemp11.to_bits().cast_signed()) & 2139095040 != 0 {fTemp11} else {0.0});
			let mut fTemp12: F32 = self.fConst3 * (self.fConst2 * self.fRec8[1] - (self.fRec32[1] + self.fRec32[2]));
			self.fRec8[0] = (if (-fTemp12.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp12} else {0.0});
			let mut fTemp13: F32 = fSlow25 * self.fRec34[1] + fSlow24 * self.fRec9[1];
			self.fRec9[0] = (if (fTemp13.to_bits().cast_signed()) & 2139095040 != 0 {fTemp13} else {0.0});
			let mut fTemp14: F32 = self.fConst3 * (self.fConst2 * self.fRec10[1] - (self.fRec35[1] + self.fRec35[2]));
			self.fRec10[0] = (if (-fTemp14.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp14} else {0.0});
			self.fVec4[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec9[0];
			let mut fTemp15: F32 = fTemp6 + 0.6 * self.fRec11[1] + self.fVec4[((i32::wrapping_sub(self.IOTA0, self.iConst17)) & 65535) as usize];
			self.fVec5[(self.IOTA0 & 8191) as usize] = fTemp15;
			let mut fTemp16: F32 = self.fVec5[((i32::wrapping_sub(self.IOTA0, self.iConst18)) & 8191) as usize];
			self.fRec11[0] = (if (fTemp16.to_bits().cast_signed()) & 2139095040 != 0 {fTemp16} else {0.0});
			let mut fTemp17: F32 = 0.6 * (fTemp6 - fTemp7);
			let mut fRec12: F32 = (if (fTemp17.to_bits().cast_signed()) & 2139095040 != 0 {fTemp17} else {0.0});
			let mut fTemp18: F32 = self.fConst3 * (self.fConst2 * self.fRec13[1] - (self.fRec33[1] + self.fRec33[2]));
			self.fRec13[0] = (if (-fTemp18.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp18} else {0.0});
			let mut fTemp19: F32 = self.fConst3 * (self.fConst2 * self.fRec14[1] - (self.fRec38[1] + self.fRec38[2]));
			self.fRec14[0] = (if (-fTemp19.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp19} else {0.0});
			let mut fTemp20: F32 = fSlow33 * self.fRec33[1] + fSlow32 * self.fRec15[1];
			self.fRec15[0] = (if (fTemp20.to_bits().cast_signed()) & 2139095040 != 0 {fTemp20} else {0.0});
			self.fVec6[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec15[0];
			let mut fTemp21: F32 = 0.6 * self.fRec16[1] + self.fVec6[((i32::wrapping_sub(self.IOTA0, self.iConst22)) & 65535) as usize];
			self.fVec7[(self.IOTA0 & 4095) as usize] = fTemp21 - fTemp6;
			let mut fTemp22: F32 = self.fVec7[((i32::wrapping_sub(self.IOTA0, self.iConst23)) & 4095) as usize];
			self.fRec16[0] = (if (fTemp22.to_bits().cast_signed()) & 2139095040 != 0 {fTemp22} else {0.0});
			let mut fTemp23: F32 = fSlow41 * self.fRec36[1] + fSlow40 * self.fRec17[1];
			self.fRec17[0] = (if (fTemp23.to_bits().cast_signed()) & 2139095040 != 0 {fTemp23} else {0.0});
			let mut fTemp24: F32 = fSlow49 * self.fRec32[1] + fSlow48 * self.fRec18[1];
			self.fRec18[0] = (if (fTemp24.to_bits().cast_signed()) & 2139095040 != 0 {fTemp24} else {0.0});
			let mut fTemp25: F32 = fSlow57 * self.fRec35[1] + fSlow56 * self.fRec19[1];
			self.fRec19[0] = (if (fTemp25.to_bits().cast_signed()) & 2139095040 != 0 {fTemp25} else {0.0});
			let mut fTemp26: F32 = self.fVec3[((i32::wrapping_sub(self.IOTA0, self.iConst30)) & 4095) as usize];
			self.fRec20[0] = (if (fTemp26.to_bits().cast_signed()) & 2139095040 != 0 {fTemp26} else {0.0});
			self.fVec8[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec19[0];
			let mut fTemp27: F32 = fTemp9 - 0.6 * self.fRec25[1] + self.fVec8[((i32::wrapping_sub(self.IOTA0, self.iConst32)) & 65535) as usize];
			self.fVec9[(self.IOTA0 & 8191) as usize] = fTemp27;
			let mut fTemp28: F32 = 0.6 * fTemp27;
			let mut fRec21: F32 = (if (fTemp28.to_bits().cast_signed()) & 2139095040 != 0 {fTemp28} else {0.0});
			self.fVec10[(self.IOTA0 & 65535) as usize] = 1e-20 + 0.35355338 * self.fRec17[0];
			let mut fTemp29: F32 = fTemp6 + 0.6 * self.fRec26[1] + self.fVec10[((i32::wrapping_sub(self.IOTA0, self.iConst34)) & 65535) as usize];
			self.fVec11[(self.IOTA0 & 8191) as usize] = fTemp29;
			let mut fTemp30: F32 = 0.6 * fTemp29;
			let mut fRec22: F32 = (if (-fTemp30.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp30} else {0.0});
			let mut fTemp31: F32 = 0.6 * (fTemp6 - fTemp21);
			let mut fRec23: F32 = (if (fTemp31.to_bits().cast_signed()) & 2139095040 != 0 {fTemp31} else {0.0});
			self.fVec12[(self.IOTA0 & 32767) as usize] = 1e-20 + 0.35355338 * self.fRec18[0];
			let mut fTemp32: F32 = self.fVec12[((i32::wrapping_sub(self.IOTA0, self.iConst36)) & 32767) as usize] - (fTemp9 + 0.6 * self.fRec29[1]);
			self.fVec13[(self.IOTA0 & 4095) as usize] = fTemp32;
			let mut fTemp33: F32 = 0.6 * fTemp32;
			let mut fRec24: F32 = (if (fTemp33.to_bits().cast_signed()) & 2139095040 != 0 {fTemp33} else {0.0});
			let mut fTemp34: F32 = self.fVec9[((i32::wrapping_sub(self.IOTA0, self.iConst37)) & 8191) as usize];
			self.fRec25[0] = (if (fTemp34.to_bits().cast_signed()) & 2139095040 != 0 {fTemp34} else {0.0});
			let mut fTemp35: F32 = self.fVec11[((i32::wrapping_sub(self.IOTA0, self.iConst38)) & 8191) as usize];
			self.fRec26[0] = (if (fTemp35.to_bits().cast_signed()) & 2139095040 != 0 {fTemp35} else {0.0});
			let mut fTemp36: F32 = 0.6 * fTemp15;
			let mut fRec27: F32 = (if (-fTemp36.to_bits().cast_signed()) & 2139095040 != 0 {-fTemp36} else {0.0});
			let mut fTemp37: F32 = fSlow65 * self.fRec38[1] + fSlow64 * self.fRec28[1];
			self.fRec28[0] = (if (fTemp37.to_bits().cast_signed()) & 2139095040 != 0 {fTemp37} else {0.0});
			let mut fTemp38: F32 = self.fVec13[((i32::wrapping_sub(self.IOTA0, self.iConst41)) & 4095) as usize];
			self.fRec29[0] = (if (fTemp38.to_bits().cast_signed()) & 2139095040 != 0 {fTemp38} else {0.0});
			self.fVec14[(self.IOTA0 & 32767) as usize] = 1e-20 + 0.35355338 * self.fRec28[0];
			let mut fTemp39: F32 = self.fVec14[((i32::wrapping_sub(self.IOTA0, self.iConst43)) & 32767) as usize] - (fTemp9 + 0.6 * self.fRec30[1]);
			self.fVec15[(self.IOTA0 & 8191) as usize] = fTemp39;
			let mut fTemp40: F32 = self.fVec15[((i32::wrapping_sub(self.IOTA0, self.iConst44)) & 8191) as usize];
			self.fRec30[0] = (if (fTemp40.to_bits().cast_signed()) & 2139095040 != 0 {fTemp40} else {0.0});
			let mut fTemp41: F32 = 0.6 * fTemp39;
			let mut fRec31: F32 = (if (fTemp41.to_bits().cast_signed()) & 2139095040 != 0 {fTemp41} else {0.0});
			let mut fTemp42: F32 = self.fRec6[1] + self.fRec11[1];
			let mut fTemp43: F32 = fRec23 + fRec22 + fRec24 + fRec7 + self.fRec29[1] + self.fRec26[1] + self.fRec20[1] + self.fRec16[1] - (fRec12 + fRec27 + fRec31 + fRec21 + self.fRec30[1] + self.fRec25[1] + fTemp42);
			self.fRec32[0] = (if (fTemp43.to_bits().cast_signed()) & 2139095040 != 0 {fTemp43} else {0.0});
			let mut fTemp44: F32 = self.fRec6[1] + self.fRec20[1];
			let mut fTemp45: F32 = self.fRec11[1] + fTemp44;
			let mut fTemp46: F32 = fRec12 + fRec27 + fRec24 + fRec7 + self.fRec29[1] + fTemp45 - (fRec23 + fRec22 + fRec31 + fRec21 + self.fRec30[1] + self.fRec26[1] + self.fRec16[1] + self.fRec25[1]);
			self.fRec33[0] = (if (fTemp46.to_bits().cast_signed()) & 2139095040 != 0 {fTemp46} else {0.0});
			let mut fTemp47: F32 = self.fRec20[1] + self.fRec11[1];
			let mut fTemp48: F32 = fRec23 + fRec27 + fRec31 + fRec7 + self.fRec30[1] + self.fRec16[1] + fTemp47 - (fRec12 + fRec22 + fRec24 + fRec21 + self.fRec29[1] + self.fRec26[1] + self.fRec6[1] + self.fRec25[1]);
			self.fRec34[0] = (if (fTemp48.to_bits().cast_signed()) & 2139095040 != 0 {fTemp48} else {0.0});
			let mut fTemp49: F32 = fRec12 + fRec22 + fRec31 + fRec7 + self.fRec30[1] + self.fRec26[1] + fTemp44 - (fRec23 + fRec27 + fRec24 + fRec21 + self.fRec29[1] + self.fRec25[1] + self.fRec11[1] + self.fRec16[1]);
			self.fRec35[0] = (if (fTemp49.to_bits().cast_signed()) & 2139095040 != 0 {fTemp49} else {0.0});
			let mut fTemp50: F32 = fRec24 + fRec31 + fRec21 + fRec7 + self.fRec30[1] + self.fRec29[1] + self.fRec20[1] + self.fRec25[1] - (fRec23 + fRec12 + fRec27 + fRec22 + self.fRec26[1] + self.fRec16[1] + fTemp42);
			self.fRec36[0] = (if (fTemp50.to_bits().cast_signed()) & 2139095040 != 0 {fTemp50} else {0.0});
			let mut fTemp51: F32 = fRec23 + fRec12 + fRec27 + fRec22 + fRec24 + fRec31 + fRec21 + fRec7 + self.fRec30[1] + self.fRec29[1] + self.fRec26[1] + self.fRec25[1] + self.fRec16[1] + fTemp45;
			self.fRec37[0] = (if (fTemp51.to_bits().cast_signed()) & 2139095040 != 0 {fTemp51} else {0.0});
			let mut fTemp52: F32 = fRec27 + fRec22 + fRec21 + fRec7 + self.fRec26[1] + self.fRec25[1] + fTemp47 - (fRec23 + fRec12 + fRec24 + fRec31 + self.fRec30[1] + self.fRec29[1] + self.fRec6[1] + self.fRec16[1]);
			self.fRec38[0] = (if (fTemp52.to_bits().cast_signed()) & 2139095040 != 0 {fTemp52} else {0.0});
			let mut fTemp53: F32 = fRec23 + fRec12 + fRec21 + fRec7 + self.fRec25[1] + self.fRec16[1] + fTemp44 - (fRec27 + fRec22 + fRec24 + fRec31 + self.fRec30[1] + self.fRec29[1] + self.fRec11[1] + self.fRec26[1]);
			self.fRec39[0] = (if (fTemp53.to_bits().cast_signed()) & 2139095040 != 0 {fTemp53} else {0.0});
			*output0 = (0.37 * (self.fRec36[0] + self.fRec38[0])) as FaustFloat;
			*output1 = (0.37 * (self.fRec36[0] - self.fRec38[0])) as FaustFloat;
			self.fRec0[1] = self.fRec0[0];
			self.fRec1[1] = self.fRec1[0];
			self.fRec2[1] = self.fRec2[0];
			self.fRec3[1] = self.fRec3[0];
			self.fRec4[1] = self.fRec4[0];
			self.fRec5[1] = self.fRec5[0];
			self.IOTA0 = i32::wrapping_add(self.IOTA0, 1);
			self.fRec6[1] = self.fRec6[0];
			self.fRec8[1] = self.fRec8[0];
			self.fRec9[1] = self.fRec9[0];
			self.fRec10[1] = self.fRec10[0];
			self.fRec11[1] = self.fRec11[0];
			self.fRec13[1] = self.fRec13[0];
			self.fRec14[1] = self.fRec14[0];
			self.fRec15[1] = self.fRec15[0];
			self.fRec16[1] = self.fRec16[0];
			self.fRec17[1] = self.fRec17[0];
			self.fRec18[1] = self.fRec18[0];
			self.fRec19[1] = self.fRec19[0];
			self.fRec20[1] = self.fRec20[0];
			self.fRec25[1] = self.fRec25[0];
			self.fRec26[1] = self.fRec26[0];
			self.fRec28[1] = self.fRec28[0];
			self.fRec29[1] = self.fRec29[0];
			self.fRec30[1] = self.fRec30[0];
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

