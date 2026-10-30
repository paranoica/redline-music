// A C ABI over signalsmith-stretch for the Rust side (src/stretch.rs). Samples are interleaved.

#include "signalsmith-stretch/signalsmith-stretch.h"

#include <new>

namespace {

using Stretch = signalsmith::stretch::SignalsmithStretch<float>;

struct Reader {
	const float *samples;
	int channels;
	struct Channel {
		const float *samples;
		int channels;
		float operator[](int i) const {
			return samples[i * channels];
		}
	};
	Channel operator[](int c) const {
		return {samples + c, channels};
	}
};

struct Writer {
	float *samples;
	int channels;
	struct Channel {
		float *samples;
		int channels;
		float &operator[](int i) const {
			return samples[i * channels];
		}
	};
	Channel operator[](int c) const {
		return {samples + c, channels};
	}
};

struct Handle {
	Stretch stretch;
	int channels = 0;
	explicit Handle(long seed) : stretch(seed) {}
};

} // namespace

extern "C" {

void *rl_stretch_create(long seed) noexcept {
	try {
		return new Handle(seed);
	} catch (...) {
		return nullptr;
	}
}

void rl_stretch_destroy(void *h) noexcept {
	delete static_cast<Handle *>(h);
}

// 0 on success; the handle is left unconfigured when allocation fails.
int rl_stretch_configure(void *h, int channels, int block, int interval) noexcept {
	auto *s = static_cast<Handle *>(h);
	try {
		s->stretch.configure(channels, block, interval);
		s->channels = channels;
		return 0;
	} catch (...) {
		s->channels = 0;
		return 1;
	}
}

void rl_stretch_set_transpose(void *h, float factor) noexcept {
	static_cast<Handle *>(h)->stretch.setTransposeFactor(factor);
}

void rl_stretch_set_formant(void *h, float factor, int compensate, float base) noexcept {
	auto &s = static_cast<Handle *>(h)->stretch;
	s.setFormantFactor(factor, compensate != 0);
	s.setFormantBase(base);
}

void rl_stretch_process(void *h, const float *input, int input_frames, float *output,
		int output_frames) noexcept {
	auto *s = static_cast<Handle *>(h);
	if (s->channels == 0) return;
	s->stretch.process(Reader{input, s->channels}, input_frames, Writer{output, s->channels},
		output_frames);
}

void rl_stretch_reset(void *h) noexcept {
	static_cast<Handle *>(h)->stretch.reset();
}

int rl_stretch_input_latency(const void *h) noexcept {
	return static_cast<const Handle *>(h)->stretch.inputLatency();
}

int rl_stretch_output_latency(const void *h) noexcept {
	return static_cast<const Handle *>(h)->stretch.outputLatency();
}

} // extern "C"
