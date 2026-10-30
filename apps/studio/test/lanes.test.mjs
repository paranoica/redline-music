import assert from "node:assert/strict";
import { test } from "node:test";

import { valueAt } from "../src/core/automation.ts";
import { peakReadout } from "../src/core/format.ts";
import {
  MAX_LANE_POINTS,
  insertPoint,
  movePoint,
  nearestPoint,
  removePoint,
  secondsToX,
  valueToY,
  withLane,
  xToSeconds,
  yToValue,
} from "../src/core/lanes.ts";

function close(got, want, tol = 1e-9) {
  assert.ok(Math.abs(got - want) <= tol, `${got} against ${want}`);
}

const DB = { low: -12, high: 12 };
const NOTE = { low: 0, high: 127, step: 1 };
const HZ = { low: 20, high: 20000, log: true };

test("time maps onto the width as the waveform maps it: 0 at the left edge, the track's end at the right", () => {
  assert.equal(secondsToX(0, 200, 1000), 0);
  assert.equal(secondsToX(50, 200, 1000), 250);
  assert.equal(secondsToX(200, 200, 1000), 1000);
  assert.equal(xToSeconds(250, 200, 1000), 50);
  assert.equal(xToSeconds(0, 200, 1000), 0);
  assert.equal(xToSeconds(1000, 200, 1000), 200);
});

test("a pointer outside the lane reads as the track's start or end", () => {
  assert.equal(xToSeconds(-10, 200, 1000), 0);
  assert.equal(xToSeconds(1200, 200, 1000), 200);
});

test("the range's top is drawn at the top, its bottom at the bottom, inside the padding", () => {
  assert.equal(valueToY(12, DB, 40, 4), 4);
  assert.equal(valueToY(-12, DB, 40, 4), 36);
  assert.equal(valueToY(0, DB, 40, 4), 20);
  assert.equal(yToValue(20, DB, 40, 4), 0);
  assert.equal(yToValue(4, DB, 40, 4), 12);
  assert.equal(yToValue(36, DB, 40, 4), -12);
});

test("a height beyond the lane gives the range's edge, never a value outside it", () => {
  assert.equal(yToValue(0, DB, 40, 4), 12);
  assert.equal(yToValue(-50, DB, 40, 4), 12);
  assert.equal(yToValue(100, DB, 40, 4), -12);
  assert.equal(valueToY(30, DB, 40, 4), 4);
  assert.equal(valueToY(-30, DB, 40, 4), 36);
});

test("a wide frequency range is logarithmic on the lane; a whole-number param snaps", () => {
  close(yToValue(20, HZ, 40, 4), Math.sqrt(20 * 20000), 1e-6);
  close(valueToY(Math.sqrt(20 * 20000), HZ, 40, 4), 20, 1e-9);
  // 0.3 of the way up 0..127 is 38.1
  assert.equal(yToValue(36 - 0.3 * 32, NOTE, 40, 4), 38);
});

test("a new point goes in time order and is returned with its index", () => {
  const points = [
    [1, 0],
    [3, 5],
  ];
  assert.deepEqual(insertPoint(points, 2, 1, DB), {
    points: [
      [1, 0],
      [2, 1],
      [3, 5],
    ],
    index: 1,
  });
  assert.deepEqual(insertPoint(points, 0.5, -2, DB), {
    points: [
      [0.5, -2],
      [1, 0],
      [3, 5],
    ],
    index: 0,
  });
  assert.deepEqual(insertPoint(points, 9, 4, DB), {
    points: [
      [1, 0],
      [3, 5],
      [9, 4],
    ],
    index: 2,
  });
  assert.deepEqual(points, [
    [1, 0],
    [3, 5],
  ]);
});

test("a new point at the time of another goes after it, so its value is the one heard there", () => {
  const got = insertPoint(
    [
      [1, 0],
      [3, 5],
    ],
    3,
    -4,
    DB,
  );
  assert.equal(got.index, 2);
  assert.equal(valueAt(got.points, 3), -4);
});

test("a new point keeps to the param's range, to seconds from 0 and to whole numbers where the param is whole", () => {
  assert.deepEqual(insertPoint([[1, 0]], 2, 99, DB).points[1], [2, 12]);
  assert.deepEqual(insertPoint([[1, 0]], 2, -99, DB).points[1], [2, -12]);
  assert.deepEqual(insertPoint([[1, 0]], -3, 0, DB), {
    points: [
      [0, 0],
      [1, 0],
    ],
    index: 0,
  });
  assert.deepEqual(insertPoint([[1, 60]], 2, 61.6, NOTE).points[1], [2, 62]);
  assert.deepEqual(insertPoint([], 4, 1, DB), { points: [[4, 1]], index: 0 });
});

test("a lane at the core's limit of points takes no more", () => {
  const full = Array.from({ length: MAX_LANE_POINTS }, (_, i) => [i, 0]);
  assert.equal(MAX_LANE_POINTS, 4096);
  assert.equal(insertPoint(full, 5000, 1, DB), null);
  assert.equal(insertPoint(full.slice(1), 5000, 1, DB).points.length, MAX_LANE_POINTS);
});

test("a moved point stays between its neighbours in time and inside the range", () => {
  const points = [
    [1, 0],
    [2, 1],
    [3, 5],
  ];
  assert.deepEqual(movePoint(points, 1, 2.5, 4, DB), [
    [1, 0],
    [2.5, 4],
    [3, 5],
  ]);
  assert.deepEqual(movePoint(points, 1, 7, 50, DB)[1], [3, 12]);
  assert.deepEqual(movePoint(points, 1, 0, -50, DB)[1], [1, -12]);
  assert.deepEqual(points[1], [2, 1]);
});

test("the first point stops at 0 s, the last moves freely later", () => {
  const points = [
    [1, 0],
    [3, 5],
  ];
  assert.deepEqual(movePoint(points, 0, -4, 2, DB)[0], [0, 2]);
  assert.deepEqual(movePoint(points, 0, 2, 2, DB)[0], [2, 2]);
  assert.deepEqual(movePoint(points, 1, 120, 2, DB)[1], [120, 2]);
  assert.deepEqual(movePoint(points, 1, 0.2, 2, DB)[1], [1, 2]);
});

test("a move of a point that is not there changes nothing", () => {
  const points = [[1, 0]];
  assert.deepEqual(movePoint(points, 3, 2, 2, DB), [[1, 0]]);
  assert.deepEqual(movePoint(points, -1, 2, 2, DB), [[1, 0]]);
});

test("removing takes out exactly that point, the first and the last included", () => {
  const points = [
    [1, 0],
    [2, 1],
    [3, 5],
  ];
  assert.deepEqual(removePoint(points, 0), [
    [2, 1],
    [3, 5],
  ]);
  assert.deepEqual(removePoint(points, 2), [
    [1, 0],
    [2, 1],
  ]);
  assert.deepEqual(removePoint([[1, 0]], 0), []);
  assert.deepEqual(removePoint(points, 5), points);
  assert.equal(points.length, 3);
});

test("a lane's new points replace only that lane; a lane left without points is dropped", () => {
  const automation = [
    { param: "mix", points: [[0, 1]] },
    { param: "note", points: [[0, 60]] },
  ];
  assert.deepEqual(withLane(automation, "note", [[1, 62]]), [
    { param: "mix", points: [[0, 1]] },
    { param: "note", points: [[1, 62]] },
  ]);
  assert.deepEqual(withLane(automation, "mix", []), [{ param: "note", points: [[0, 60]] }]);
  assert.deepEqual(automation[0], { param: "mix", points: [[0, 1]] });
});

test("the point under the pointer is the closest one within the radius, or none", () => {
  const pixels = [
    [10, 10],
    [20, 10],
    [100, 30],
  ];
  assert.equal(nearestPoint(pixels, 11, 11, 6), 0);
  assert.equal(nearestPoint(pixels, 18, 10, 6), 1);
  assert.equal(nearestPoint(pixels, 106, 30, 6), 2);
  assert.equal(nearestPoint(pixels, 107, 30, 6), -1);
  assert.equal(nearestPoint(pixels, 60, 20, 6), -1);
  assert.equal(nearestPoint([], 0, 0, 6), -1);
});

test("a meter reads as idle before anything played, and as silence only while playing", () => {
  assert.equal(peakReadout(-Infinity, false), "—");
  assert.equal(peakReadout(-Infinity, true), "−∞");
  assert.equal(peakReadout(-6.4, true), "−6.4");
  assert.equal(peakReadout(-6.4, false), "−6.4");
});
