// The room reverb: zita-rev1's eight-line feedback delay network, no pre-delay (the effect
// adds its own). Also the reverb inside gated_reverb and reverse_reverb.
declare name "room";
import("stdfaust.lib");

decay = hslider("decay", 0.8, 0.1, 30, 0.01);
damping = hslider("damping", 6000, 500, 20000, 1);

process = re.zita_rev1_stereo(0, 200, damping, decay, decay, 192000);
