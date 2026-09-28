//! Menu bar glyphs.
//!
//! The status item shows Gunther's own mark, the single-stroke "G" whose
//! crossbar ends in a knowledge node, drawn procedurally so no image files ship
//! with the app. Calm states are macOS template images (black, recoloured by
//! the system for light and dark menu bars). Only live capture leaves the
//! template: the mark steps back to grey and its node becomes a red recording
//! light, or amber pause bars. Every state differs by shape, not only colour.

/// macOS draws status items 18 pt tall; 36 px keeps the mark sharp at 2x.
pub const GLYPH_SIZE: u32 = 36;

const VIEWBOX: f32 = 32.0;
const CENTER: (f32, f32) = (16.0, 16.0);
const RING_RADIUS: f32 = 11.0;
const STROKE: f32 = 1.35;
const GAP_START: f32 = -std::f32::consts::FRAC_PI_4;
const BAR_END: (f32, f32) = (27.0, 16.0);
const NODE_RADIUS: f32 = 2.8;
const BADGE: (f32, f32) = (27.6, 8.4);
const BADGE_RADIUS: f32 = 3.3;
const BADGE_GAP: f32 = 1.5;

const TEMPLATE_INK: [u8; 3] = [0, 0, 0];
const RECEDED_INK: [u8; 3] = [142, 142, 147];
const RECORDING_RED: [u8; 3] = [255, 69, 58];
const PAUSED_AMBER: [u8; 3] = [255, 159, 10];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GlyphVariant {
    /// Nothing is happening: the plain mark.
    Idle,
    /// Unsaved text or a file waits in Capture: a badge in the G's opening.
    Draft,
    /// A finished recording waits for review: the same badge.
    Review,
    /// Opening the microphone, importing, finalising or saving: a hollow node.
    Preparing,
    /// Live audio: grey mark, red recording light.
    Recording,
    /// Paused audio: grey mark, amber pause bars.
    Paused,
    /// Capture needs attention: grey mark, red badge.
    Attention,
}

pub struct Glyph {
    pub rgba: Vec<u8>,
    pub template: bool,
}

fn length(x: f32, y: f32) -> f32 {
    (x * x + y * y).sqrt()
}

/// Distance from `p` to the segment `a`–`b`.
fn segment_distance(p: (f32, f32), a: (f32, f32), b: (f32, f32)) -> f32 {
    let (px, py) = (p.0 - a.0, p.1 - a.1);
    let (bx, by) = (b.0 - a.0, b.1 - a.1);
    let t = ((px * bx + py * by) / (bx * bx + by * by)).clamp(0.0, 1.0);
    length(px - bx * t, py - by * t)
}

/// Signed distance to the G's open ring (round caps at both ends of the gap).
fn ring(p: (f32, f32)) -> f32 {
    let (dx, dy) = (p.0 - CENTER.0, p.1 - CENTER.1);
    let angle = dy.atan2(dx);
    if angle > GAP_START && angle < 0.0 {
        let start = (
            CENTER.0 + RING_RADIUS * GAP_START.cos(),
            CENTER.1 + RING_RADIUS * GAP_START.sin(),
        );
        let end = (CENTER.0 + RING_RADIUS, CENTER.1);
        length(p.0 - start.0, p.1 - start.1).min(length(p.0 - end.0, p.1 - end.1)) - STROKE
    } else {
        (length(dx, dy) - RING_RADIUS).abs() - STROKE
    }
}

fn crossbar(p: (f32, f32), inner_end: f32) -> f32 {
    segment_distance(p, BAR_END, (inner_end, CENTER.1)) - STROKE
}

fn disc(p: (f32, f32), center: (f32, f32), radius: f32) -> f32 {
    length(p.0 - center.0, p.1 - center.1) - radius
}

fn rounded_box(p: (f32, f32), center: (f32, f32), half: (f32, f32), radius: f32) -> f32 {
    let qx = (p.0 - center.0).abs() - half.0 + radius;
    let qy = (p.1 - center.1).abs() - half.1 + radius;
    length(qx.max(0.0), qy.max(0.0)) + qx.max(qy).min(0.0) - radius
}

/// Coverage of one layer for a pixel, from 4x4 supersamples of a signed distance.
fn coverage(px: u32, py: u32, shape: &dyn Fn((f32, f32)) -> f32) -> f32 {
    let scale = VIEWBOX / GLYPH_SIZE as f32;
    let mut inside = 0;
    for sy in 0..4 {
        for sx in 0..4 {
            let x = (px as f32 + (sx as f32 + 0.5) / 4.0) * scale;
            let y = (py as f32 + (sy as f32 + 0.5) / 4.0) * scale;
            if shape((x, y)) <= 0.0 {
                inside += 1;
            }
        }
    }
    inside as f32 / 16.0
}

struct Layer<'a> {
    color: [u8; 3],
    shape: &'a dyn Fn((f32, f32)) -> f32,
}

fn composite(layers: &[Layer], template: bool) -> Glyph {
    let mut rgba = vec![0_u8; (GLYPH_SIZE * GLYPH_SIZE * 4) as usize];
    for y in 0..GLYPH_SIZE {
        for x in 0..GLYPH_SIZE {
            // Straight-alpha "source over" of each layer, bottom to top.
            let (mut r, mut g, mut b, mut a) = (0.0_f32, 0.0_f32, 0.0_f32, 0.0_f32);
            for layer in layers {
                let alpha = coverage(x, y, layer.shape);
                if alpha <= 0.0 {
                    continue;
                }
                let out = alpha + a * (1.0 - alpha);
                let mix = |source: u8, dest: f32| {
                    (source as f32 * alpha + dest * a * (1.0 - alpha)) / out
                };
                r = mix(layer.color[0], r);
                g = mix(layer.color[1], g);
                b = mix(layer.color[2], b);
                a = out;
            }
            let index = ((y * GLYPH_SIZE + x) * 4) as usize;
            rgba[index] = r.round() as u8;
            rgba[index + 1] = g.round() as u8;
            rgba[index + 2] = b.round() as u8;
            rgba[index + 3] = (a * 255.0).round() as u8;
        }
    }
    Glyph { rgba, template }
}

pub fn render(variant: GlyphVariant) -> Glyph {
    let badge_cut = |p: (f32, f32)| disc(p, BADGE, BADGE_RADIUS + BADGE_GAP);
    let with_badge = matches!(
        variant,
        GlyphVariant::Draft | GlyphVariant::Review | GlyphVariant::Attention
    );
    // A badge sits in the G's opening, separated from the stroke by a clear gap.
    let mark = move |p: (f32, f32)| {
        let inner_end = if variant == GlyphVariant::Paused {
            21.4
        } else {
            18.4
        };
        let stroke = ring(p).min(crossbar(p, inner_end));
        if with_badge {
            stroke.max(-badge_cut(p))
        } else {
            stroke
        }
    };
    let node = |p: (f32, f32)| disc(p, CENTER, NODE_RADIUS);
    let hollow_node = |p: (f32, f32)| (length(p.0 - CENTER.0, p.1 - CENTER.1) - 2.4).abs() - 0.8;
    let recording_light = |p: (f32, f32)| disc(p, CENTER, 3.9);
    let pause_bars = |p: (f32, f32)| {
        rounded_box(p, (14.3, 16.0), (0.95, 3.1), 0.6).min(rounded_box(
            p,
            (17.9, 16.0),
            (0.95, 3.1),
            0.6,
        ))
    };
    let badge = |p: (f32, f32)| disc(p, BADGE, BADGE_RADIUS);

    match variant {
        GlyphVariant::Idle => composite(
            &[Layer {
                color: TEMPLATE_INK,
                shape: &|p| mark(p).min(node(p)),
            }],
            true,
        ),
        GlyphVariant::Draft | GlyphVariant::Review => composite(
            &[Layer {
                color: TEMPLATE_INK,
                shape: &|p| mark(p).min(node(p)).min(badge(p)),
            }],
            true,
        ),
        GlyphVariant::Preparing => composite(
            &[Layer {
                color: TEMPLATE_INK,
                shape: &|p| mark(p).min(hollow_node(p)),
            }],
            true,
        ),
        GlyphVariant::Recording => composite(
            &[
                Layer {
                    color: RECEDED_INK,
                    shape: &|p| mark(p).max(-disc(p, CENTER, 5.2)),
                },
                Layer {
                    color: RECORDING_RED,
                    shape: &recording_light,
                },
            ],
            false,
        ),
        GlyphVariant::Paused => composite(
            &[
                Layer {
                    color: RECEDED_INK,
                    shape: &mark,
                },
                Layer {
                    color: PAUSED_AMBER,
                    shape: &pause_bars,
                },
            ],
            false,
        ),
        GlyphVariant::Attention => composite(
            &[
                Layer {
                    color: RECEDED_INK,
                    shape: &|p| mark(p).min(node(p)),
                },
                Layer {
                    color: RECORDING_RED,
                    shape: &badge,
                },
            ],
            false,
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::{render, GlyphVariant, GLYPH_SIZE};

    /// Pixel at a viewBox coordinate (0–32 on each axis).
    fn pixel(rgba: &[u8], x: f32, y: f32) -> [u8; 4] {
        let px = (x / 32.0 * GLYPH_SIZE as f32) as u32;
        let py = (y / 32.0 * GLYPH_SIZE as f32) as u32;
        let index = ((py * GLYPH_SIZE + px) * 4) as usize;
        [
            rgba[index],
            rgba[index + 1],
            rgba[index + 2],
            rgba[index + 3],
        ]
    }

    #[test]
    fn calm_states_are_template_images_of_the_mark() {
        for variant in [
            GlyphVariant::Idle,
            GlyphVariant::Draft,
            GlyphVariant::Review,
            GlyphVariant::Preparing,
        ] {
            let glyph = render(variant);
            assert!(
                glyph.template,
                "{variant:?} must adapt to light and dark menu bars"
            );
            assert_eq!(glyph.rgba.len(), (GLYPH_SIZE * GLYPH_SIZE * 4) as usize);
            // Left of the ring and the bottom of the ring are inked.
            assert!(pixel(&glyph.rgba, 5.0, 16.0)[3] > 200, "{variant:?} ring");
            assert!(
                pixel(&glyph.rgba, 16.0, 27.0)[3] > 200,
                "{variant:?} ring bottom"
            );
        }
    }

    #[test]
    fn the_idle_mark_never_looks_like_a_record_button() {
        let idle = render(GlyphVariant::Idle);
        // The node is small and solid; the crossbar reaches it from the right.
        assert!(pixel(&idle.rgba, 16.0, 16.0)[3] > 200);
        assert!(pixel(&idle.rgba, 23.0, 16.0)[3] > 200, "crossbar");
        // The G is open at the upper right, and has no frame corners.
        assert_eq!(pixel(&idle.rgba, 26.5, 11.5)[3], 0, "opening");
        assert_eq!(pixel(&idle.rgba, 1.0, 1.0)[3], 0, "no corner brackets");
        // Nothing red anywhere.
        assert!(idle
            .rgba
            .chunks(4)
            .all(|px| px[0] == 0 && px[1] == 0 && px[2] == 0));
    }

    #[test]
    fn waiting_work_adds_a_badge_in_the_opening() {
        let idle = render(GlyphVariant::Idle);
        let draft = render(GlyphVariant::Draft);
        assert_eq!(pixel(&idle.rgba, 27.6, 8.4)[3], 0);
        assert!(pixel(&draft.rgba, 27.6, 8.4)[3] > 200);
    }

    #[test]
    fn preparing_hollows_the_node() {
        let preparing = render(GlyphVariant::Preparing);
        assert_eq!(pixel(&preparing.rgba, 16.0, 16.0)[3], 0, "hollow centre");
        assert!(pixel(&preparing.rgba, 13.6, 16.0)[3] > 150, "node ring");
    }

    #[test]
    fn live_capture_uses_shape_and_colour() {
        let recording = render(GlyphVariant::Recording);
        assert!(!recording.template);
        assert_eq!(
            pixel(&recording.rgba, 16.0, 16.0),
            [255, 69, 58, 255],
            "red light"
        );
        assert_eq!(
            &pixel(&recording.rgba, 5.0, 16.0)[..3],
            &[142, 142, 147],
            "receded mark"
        );

        let paused = render(GlyphVariant::Paused);
        assert!(!paused.template);
        assert_eq!(
            &pixel(&paused.rgba, 14.3, 16.0)[..3],
            &[255, 159, 10],
            "left bar"
        );
        assert_eq!(
            &pixel(&paused.rgba, 17.9, 16.0)[..3],
            &[255, 159, 10],
            "right bar"
        );
        assert_eq!(
            pixel(&paused.rgba, 16.1, 16.0)[3],
            0,
            "gap between the bars"
        );

        let attention = render(GlyphVariant::Attention);
        assert_eq!(
            &pixel(&attention.rgba, 27.6, 8.4)[..3],
            &[255, 69, 58],
            "red badge"
        );
    }

    /// `GUNTHER_GLYPH_DUMP=/tmp/glyphs cargo test --lib dump_glyphs -- --ignored`
    /// writes raw RGBA files for visual review.
    #[test]
    #[ignore]
    fn dump_glyphs() {
        let Ok(directory) = std::env::var("GUNTHER_GLYPH_DUMP") else {
            return;
        };
        std::fs::create_dir_all(&directory).unwrap();
        for variant in [
            GlyphVariant::Idle,
            GlyphVariant::Draft,
            GlyphVariant::Review,
            GlyphVariant::Preparing,
            GlyphVariant::Recording,
            GlyphVariant::Paused,
            GlyphVariant::Attention,
        ] {
            let glyph = render(variant);
            std::fs::write(format!("{directory}/{variant:?}.rgba"), glyph.rgba).unwrap();
        }
    }
}
