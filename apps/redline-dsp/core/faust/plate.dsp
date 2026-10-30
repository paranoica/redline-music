// The plate reverb: Dattorro's figure-of-eight tank. Its delays are fixed in samples, so the
// decay in seconds becomes a gain per pass at the running rate: the tank's loop is 19162
// samples long and passes the gain four times.
declare name "plate";
import("stdfaust.lib");

decay = hslider("decay", 1.5, 0.1, 30, 0.01);
damping = hslider("damping", 0.3, 0, 0.95, 0.01);

gain = min(0.9999, pow(10, -3 * 19162 / (4 * ma.SR * decay)));

process = re.dattorro_rev(0, 0.9995, 0.75, 0.625, gain, 0.7, 0.5, damping);
