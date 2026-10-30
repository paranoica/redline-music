// The shimmer reverb: zita-rev1 with its output pitch shifted and fed back into its input,
// so every pass through the loop climbs by the shift. tanh in the loop keeps a feedback
// that rings louder than it decays bounded.
declare name "shimmer";
import("stdfaust.lib");

decay = hslider("decay", 4, 0.1, 30, 0.01);
damping = hslider("damping", 8000, 500, 20000, 1);
shift = hslider("shift", 12, -12, 12, 0.01);
feedback = hslider("feedback", 0.5, 0, 0.9, 0.001);

window = 2048;
up = ef.transpose_windowed(4, window, shift) : *(feedback) : ma.tanh;
mixin = ro.interleave(2, 2) : +, +;

process = (mixin : re.zita_rev1_stereo(0, 200, damping, decay, decay, 192000)) ~ (up, up);
