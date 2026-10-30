// The hall reverb: jpverb, a diffuser network with slowly modulated delays.
declare name "hall";
import("stdfaust.lib");

decay = hslider("decay", 3, 0.5, 30, 0.01);
damping = hslider("damping", 0.3, 0, 0.95, 0.01);
size = hslider("size", 2, 0.5, 5, 0.01);

process = re.jpverb(decay, damping, size, 0.707, 0.1, 2, 1, 1, 1, 500, 2000);
