use super::Voice;

/// The recorded zones a sampler plays, loaded by the host before playing.
#[derive(Default)]
pub struct SampleBank {}

pub fn make(_instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    None
}
