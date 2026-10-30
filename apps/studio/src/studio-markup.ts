// The studio page's markup, put into the root the app is mounted on. Elements are found by data-ref.

export const studioMarkup = `
  <p class="narrow">Студия — инструмент для экрана шире 1024 пикселей. Растяни окно или открой её на компьютере.</p>

  <div class="app">
    <header class="deck">
      <div class="deck__brand">
        <span class="brand">Redline</span>
        <span class="deck__track" data-ref="track-name">загрузка</span>
      </div>

      <div class="deck__transport" role="group" aria-label="Воспроизведение">
        <button class="play" data-ref="play" type="button" aria-pressed="false" aria-label="Играть (пробел)">
          <svg class="play__icon play__icon--play" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z"/></svg>
          <svg class="play__icon play__icon--pause" viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z"/></svg>
        </button>
        <button class="toggle" data-ref="loop" type="button" aria-pressed="true" title="Петля (L)">Петля <kbd>L</kbd></button>
        <p class="clock"><span class="clock__now num" data-ref="time">00:00.0</span><span class="clock__total num"><span aria-hidden="true">/</span> <span data-ref="total">00:00.0</span></span></p>
      </div>

      <div class="deck__ab" data-ref="ab">
        <div class="deck__ref" data-ref="ref-pick"></div>
        <p class="deck__gain" data-ref="ab-gain" aria-live="polite"></p>
      </div>

      <div class="deck__actions">
        <span class="analysis" data-ref="analysis-state" data-state="done">анализ свежий</span>
        <button class="btn btn--line deck__render" data-ref="render" type="button">Точный рендер</button>
        <button class="btn btn--signal" data-ref="save" type="button" disabled>Сохранить</button>
      </div>
    </header>

    <p class="status" data-ref="status" role="status" aria-live="polite"></p>

    <section class="wave" data-ref="wave" tabindex="0" role="slider" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"
      aria-label="Трек: стрелки перематывают, [ и ] ставят края петли на позицию">
      <canvas class="wave__canvas" data-ref="wave-canvas" data-cursor="seek"></canvas>
      <p class="wave__legend">
        <span class="wave__key wave__key--red">красное сверху — где мастер срезал громкость, рисуется по ходу</span>
        <span class="wave__key">клик — перемотка · края петли тянутся · Shift + протяжка — новая петля</span>
      </p>
    </section>
    <section class="lanes" data-ref="lanes" aria-label="Автоматизация рэка" hidden></section>

    <div class="tools" data-ref="tools"></div>

    <div class="layout">
      <main class="desk" data-ref="desk"></main>
      <div data-ref="strip-slot"></div>
    </div>
  </div>
`;
