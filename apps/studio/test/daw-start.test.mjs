// The start screen's pure parts: the song name rule (the server's folder-name rule), the
// "изменена …" text, which view the page opens with, the new song request, and the words on a card.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../src/song/api.ts";
import { beatFileName, chooseView, genreName, NAME_RULE, nameProblem, newSongRequest, openRefusal, relativeText, songMeta, textLabel } from "../src/daw/start.ts";

// ---------- the name ----------

test("a name that is one folder name passes", () => {
  for (const name of ["test", "ночная 2", "a.b", "x-y_z", "a..b"]) assert.equal(nameProblem(name, []), null, name);
});

test("an empty name asks for one", () => {
  assert.equal(nameProblem("", []), "Назови песню.");
  assert.equal(nameProblem("   ", []), "Назови песню.");
});

test("a name the server refuses gets the rule, whichever part breaks it", () => {
  for (const name of [" a", "a ", ".", "..", ".hidden", "a/b", "a\\b", "a\tb", "a\nb", "\u0001"]) {
    assert.equal(nameProblem(name, []), NAME_RULE, JSON.stringify(name));
  }
  // the rule names what it forbids
  assert.match(NAME_RULE, /\//);
  assert.match(NAME_RULE, /\\/);
  assert.match(NAME_RULE, /точк/);
  assert.match(NAME_RULE, /пробел/);
});

test("a name already taken is refused by name, an unrelated one is not", () => {
  assert.equal(nameProblem("test", ["test", "other"]), "Песня «test» уже есть.");
  assert.equal(nameProblem("test2", ["test", "other"]), null);
});

// ---------- relative time ----------

const now = new Date(2026, 9, 31, 15, 0, 0);
const at = (...parts) => new Date(...parts).toISOString();
const ago = (ms) => new Date(now.getTime() - ms).toISOString();
const MIN = 60_000;

test("under a minute, or a time ahead of the clock, is just now", () => {
  assert.equal(relativeText(ago(0), now), "только что");
  assert.equal(relativeText(ago(59_000), now), "только что");
  assert.equal(relativeText(ago(-5 * MIN), now), "только что");
});

test("minutes take the Russian plural", () => {
  assert.equal(relativeText(ago(MIN), now), "1 минуту назад");
  assert.equal(relativeText(ago(2 * MIN), now), "2 минуты назад");
  assert.equal(relativeText(ago(5 * MIN), now), "5 минут назад");
  assert.equal(relativeText(ago(11 * MIN), now), "11 минут назад");
  assert.equal(relativeText(ago(21 * MIN), now), "21 минуту назад");
  assert.equal(relativeText(ago(59 * MIN), now), "59 минут назад");
});

test("hours on the same day, yesterday by the calendar", () => {
  assert.equal(relativeText(ago(60 * MIN), now), "1 час назад");
  assert.equal(relativeText(ago(3 * 60 * MIN), now), "3 часа назад");
  assert.equal(relativeText(at(2026, 9, 31, 0, 0, 0), now), "15 часов назад");
  assert.equal(relativeText(at(2026, 9, 30, 23, 59, 0), now), "вчера");
  assert.equal(relativeText(at(2026, 9, 30, 0, 0, 0), now), "вчера");
  // two hours ago but before midnight is yesterday
  const night = new Date(2026, 9, 31, 1, 0, 0);
  assert.equal(relativeText(at(2026, 9, 30, 23, 0, 0), night), "вчера");
});

test("days up to a week, then a week, then the date", () => {
  assert.equal(relativeText(at(2026, 9, 29, 12, 0, 0), now), "2 дня назад");
  assert.equal(relativeText(at(2026, 9, 26, 12, 0, 0), now), "5 дней назад");
  assert.equal(relativeText(at(2026, 9, 25, 12, 0, 0), now), "6 дней назад");
  assert.equal(relativeText(at(2026, 9, 24, 12, 0, 0), now), "неделю назад");
  assert.equal(relativeText(at(2026, 9, 18, 12, 0, 0), now), "неделю назад");
  assert.equal(relativeText(at(2026, 9, 17, 12, 0, 0), now), "17 октября");
  assert.equal(relativeText(at(2026, 0, 3, 12, 0, 0), now), "3 января");
  assert.equal(relativeText(at(2025, 11, 30, 12, 0, 0), now), "30 декабря 2025");
});

test("a time that does not parse gives no text", () => {
  assert.equal(relativeText("", now), "");
  assert.equal(relativeText("not a date", now), "");
});

// ---------- which view ----------

test("the page opens the song when one is active, the list when none is", () => {
  assert.equal(chooseView({ active: "test", listed: true, asked: false }), "song");
  assert.equal(chooseView({ active: null, listed: true, asked: false }), "start");
});

test("a press on the song name shows the list even with a song open", () => {
  assert.equal(chooseView({ active: "test", listed: true, asked: true }), "start");
  assert.equal(chooseView({ active: null, listed: false, asked: true }), "start");
});

test("a server that cannot list songs serves its one song", () => {
  assert.equal(chooseView({ active: null, listed: false, asked: false }), "song");
});

// ---------- the new song request ----------

const form = (over = {}) => ({ name: "новая", beat: "file", file: "input/beats/a b.mp3", seed: null, text: "none", textSeed: null, ...over });

test("a type beat without text makes the server's body", () => {
  assert.deepEqual(newSongRequest(form(), []), { ok: true, body: { name: "новая", beat: { file: "input/beats/a b.mp3" }, text_seed: null } });
});

test("an own beat with a written text makes the server's body; seed 0 counts", () => {
  const got = newSongRequest(form({ beat: "own", file: "input/beats/a.mp3", seed: 0, text: "seed", textSeed: 7 }), []);
  assert.deepEqual(got, { ok: true, body: { name: "новая", beat: { own: 0 }, text_seed: 7 } });
});

test("a new beat sends its genre and kit; no genre is a drawn one", () => {
  const got = newSongRequest(form({ beat: "new", file: null, genre: "uk_drill", kit: "synth" }), []);
  assert.deepEqual(got, { ok: true, body: { name: "новая", beat: { new: { genre: "uk_drill", kit: "synth" } }, text_seed: null } });
  const drawn = newSongRequest(form({ beat: "new", genre: null }), []);
  assert.deepEqual(drawn.ok && drawn.body.beat, { new: { genre: null, kit: "auto" } });
});

test("a genre shows by its Russian name, an unknown one by its words", () => {
  assert.equal(genreName("uk_drill"), "UK дрилл");
  assert.equal(genreName("new_wave"), "new wave");
});

test("a missing choice is named, the name first", () => {
  assert.deepEqual(newSongRequest(form({ name: "" }), []), { ok: false, problem: "Назови песню." });
  assert.deepEqual(newSongRequest(form({ name: "a/b", file: null }), []), { ok: false, problem: NAME_RULE });
  assert.deepEqual(newSongRequest(form({ file: null }), []), { ok: false, problem: "Выбери тайп-бит." });
  assert.deepEqual(newSongRequest(form({ beat: "own", seed: null }), []), { ok: false, problem: "Выбери свой бит." });
  assert.deepEqual(newSongRequest(form({ text: "seed", textSeed: null }), []), { ok: false, problem: "Выбери текст." });
  assert.deepEqual(newSongRequest(form({ name: "test" }), ["test"]), { ok: false, problem: "Песня «test» уже есть." });
});

// ---------- card and picker words ----------

test("a beat file shows by its file name", () => {
  assert.equal(beatFileName("input/beats/a b.mp3"), "a b.mp3");
  assert.equal(beatFileName("x.wav"), "x.wav");
});

test("a text shows by its title, an untitled one by its seed", () => {
  assert.equal(textLabel({ seed: 4, title: "ночь" }), "ночь");
  assert.equal(textLabel({ seed: 4, title: "" }), "без названия 4");
});

test("a card says the beat kind and label, and the takes in Russian plural", () => {
  const s = (over) => ({ name: "s", beat: "file", beat_label: "a.mp3", takes: 0, text: true, modified: "", ...over });
  assert.deepEqual(songMeta(s({})), ["тайп-бит · a.mp3", "дублей нет"]);
  assert.deepEqual(songMeta(s({ beat: "own", beat_label: "seed 3", takes: 1 })), ["свой бит · seed 3", "1 дубль"]);
  assert.deepEqual(songMeta(s({ beat: "flip", beat_label: "a.mp3, flip 2", takes: 3, text: false })), ["флип · a.mp3, flip 2", "3 дубля · без текста"]);
  assert.equal(songMeta(s({ takes: 5 }))[1], "5 дублей");
  assert.equal(songMeta(s({ takes: 11 }))[1], "11 дублей");
  assert.equal(songMeta(s({ takes: 21 }))[1], "21 дубль");
});

// ---------- refusals on open ----------

test("a refused open says why in one line, by code", () => {
  const say = (code, error = "server words") => openRefusal(new ApiError(409, { code, error }));
  assert.equal(say("take_running"), "Идёт запись дубля: дождись её конца.");
  assert.equal(say("engine_busy"), "Движок записи открывается или калибруется: подожди немного.");
  assert.equal(say("busy"), "Прошлая песня ещё занята: попробуй через пару секунд.");
  assert.equal(say("something_new"), "Не открылась: server words");
  assert.equal(openRefusal(new Error("network down")), "Не открылась: network down");
});
