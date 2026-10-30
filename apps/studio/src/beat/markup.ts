// The beat editor's markup, put into the root the app is mounted on. Elements are found by data-ref.

export const beatMarkup = `
  <p class="narrow">Редактор бита — инструмент для экрана шире 1024 пикселей. Растяни окно или открой его на компьютере.</p>

  <div class="app">
    <header class="deck bdeck">
      <div class="deck__brand">
        <span class="brand">Redline</span>
        <span class="deck__track" data-ref="seed-name">бит · загрузка</span>
      </div>

      <div class="deck__transport" role="group" aria-label="Воспроизведение">
        <button class="play" data-ref="play" type="button" aria-pressed="false" aria-label="Играть (пробел)">
          <svg class="play__icon play__icon--play" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z"/></svg>
          <svg class="play__icon play__icon--pause" viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z"/></svg>
        </button>
        <button class="toggle" data-ref="play-from" type="button" title="Играть с выбранного такта (двойной клик по такту — то же)">С такта</button>
        <button class="toggle" data-ref="loop" type="button" aria-pressed="false" title="Крутить выбранную секцию по кругу">Петля секции</button>
        <p class="clock" aria-label="Позиция: такт и доля">
          <span class="clock__now num" data-ref="pos">1:1</span>
          <span class="clock__total num"><span aria-hidden="true">/</span> <span data-ref="bars">0</span> такт.</span>
        </p>
      </div>

      <div class="bdeck__facts">
        <span class="bdeck__fact num" data-ref="tempo">— BPM</span>
        <span class="bdeck__fact" data-ref="key">—</span>
        <div data-ref="meter-slot"></div>
      </div>

      <div class="deck__actions">
        <button class="checkstate" data-ref="check-state" type="button" data-state="busy" aria-live="polite">проверяю</button>
        <button class="btn btn--ghost" data-ref="undo" type="button" title="Ctrl+Z" disabled>Отменить</button>
        <button class="btn btn--ghost" data-ref="redo" type="button" title="Ctrl+Shift+Z" disabled>Вернуть</button>
        <button class="btn btn--line deck__render" data-ref="render" type="button">Точный рендер</button>
        <button class="btn btn--signal" data-ref="save" type="button" disabled>Сохранить</button>
      </div>
    </header>

    <p class="status" data-ref="status" role="status" aria-live="polite"></p>
    <section class="issues" data-ref="issues" aria-label="Проверка" tabindex="-1" hidden></section>
    <section class="render-out" data-ref="render-out" aria-label="Точный рендер" hidden></section>

    <div data-ref="song-slot"></div>

    <div class="work">
      <main class="work__main" data-ref="work-main"></main>
      <div class="work__side" data-ref="side-slot"></div>
    </div>
  </div>
`;
