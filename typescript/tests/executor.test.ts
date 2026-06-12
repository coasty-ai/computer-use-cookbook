/**
 * executor.ts — defensive dispatch over BOTH documented param shapes
 * (Reference §6 vs the local-automation section), coordinate scaling,
 * `raw` logged-and-skipped, terminal markers, and the NullBackend.
 */
import { describe, expect, it } from 'vitest';

import {
  ActionParamError,
  NullBackend,
  executeAction,
  executeActions,
} from '../src/coasty/executor.js';
import { type Action } from '../src/coasty/types.js';

function action(actionType: Action['action_type'], params: Record<string, unknown> = {}): Action {
  return { action_type: actionType, params };
}

describe('click', () => {
  it('clicks at {x, y} with left/1 defaults (Reference §6 shape)', async () => {
    const backend = new NullBackend();
    const result = await executeAction(action('click', { x: 100, y: 200 }), backend);
    expect(result).toEqual({ actionType: 'click', executed: true, terminal: null, detail: null });
    expect(backend.calls).toEqual([
      { method: 'click', args: [100, 200, { button: 'left', clicks: 1 }] },
    ]);
  });

  it('honors the optional button/clicks (local-automation shape)', async () => {
    const backend = new NullBackend();
    await executeAction(action('click', { x: 5, y: 6, button: 'right', clicks: 2 }), backend);
    expect(backend.calls[0]?.args[2]).toEqual({ button: 'right', clicks: 2 });
  });

  it('throws ActionParamError when coordinates are missing', async () => {
    await expect(executeAction(action('click', { x: 1 }), new NullBackend())).rejects.toThrow(
      ActionParamError,
    );
  });
});

describe('key_press — both shapes', () => {
  it('accepts {key: "enter"} (Reference §6)', async () => {
    const backend = new NullBackend();
    await executeAction(action('key_press', { key: 'enter' }), backend);
    expect(backend.calls).toEqual([{ method: 'keyPress', args: ['enter'] }]);
  });

  it('accepts {keys: [...]} pressed in order (local-automation)', async () => {
    const backend = new NullBackend();
    await executeAction(action('key_press', { keys: ['tab', 'enter'] }), backend);
    expect(backend.calls).toEqual([
      { method: 'keyPress', args: ['tab'] },
      { method: 'keyPress', args: ['enter'] },
    ]);
  });

  it('throws when neither key nor keys is present', async () => {
    await expect(executeAction(action('key_press'), new NullBackend())).rejects.toThrow(
      ActionParamError,
    );
  });
});

describe('key_combo', () => {
  it('presses the chord together', async () => {
    const backend = new NullBackend();
    await executeAction(action('key_combo', { keys: ['ctrl', 'c'] }), backend);
    expect(backend.calls).toEqual([{ method: 'keyCombo', args: [['ctrl', 'c']] }]);
  });
});

describe('wait — both shapes', () => {
  it('accepts {ms} (Reference §6)', async () => {
    const backend = new NullBackend();
    await executeAction(action('wait', { ms: 250 }), backend);
    expect(backend.calls).toEqual([{ method: 'wait', args: [250] }]);
  });

  it('accepts {seconds} and converts to ms (local-automation)', async () => {
    const backend = new NullBackend();
    await executeAction(action('wait', { seconds: 2 }), backend);
    expect(backend.calls).toEqual([{ method: 'wait', args: [2000] }]);
  });

  it('throws when neither ms nor seconds is present', async () => {
    await expect(executeAction(action('wait'), new NullBackend())).rejects.toThrow(
      ActionParamError,
    );
  });
});

describe('scroll — both shapes', () => {
  it('accepts {direction, amount} (Reference §6)', async () => {
    const backend = new NullBackend();
    await executeAction(action('scroll', { direction: 'down', amount: 3, x: 10, y: 20 }), backend);
    expect(backend.calls).toEqual([
      { method: 'scroll', args: [{ direction: 'down', amount: 3, x: 10, y: 20 }] },
    ]);
  });

  it('accepts signed {clicks}: positive scrolls up (local-automation)', async () => {
    const backend = new NullBackend();
    await executeAction(action('scroll', { clicks: 2 }), backend);
    expect(backend.calls).toEqual([
      { method: 'scroll', args: [{ direction: 'up', amount: 2, x: null, y: null }] },
    ]);
  });

  it('accepts signed {clicks}: negative scrolls down', async () => {
    const backend = new NullBackend();
    await executeAction(action('scroll', { clicks: -4 }), backend);
    expect(backend.calls).toEqual([
      { method: 'scroll', args: [{ direction: 'down', amount: 4, x: null, y: null }] },
    ]);
  });

  it('throws when neither shape is satisfied', async () => {
    await expect(
      executeAction(action('scroll', { direction: 'down' }), new NullBackend()),
    ).rejects.toThrow(ActionParamError);
    await expect(executeAction(action('scroll'), new NullBackend())).rejects.toThrow(
      ActionParamError,
    );
  });
});

describe('drag — both shapes', () => {
  it('accepts {from_x, from_y, to_x, to_y} (Reference §6)', async () => {
    const backend = new NullBackend();
    await executeAction(action('drag', { from_x: 1, from_y: 2, to_x: 3, to_y: 4 }), backend);
    expect(backend.calls).toEqual([{ method: 'drag', args: [1, 2, 3, 4] }]);
  });

  it('accepts {x1, y1, x2, y2} (local-automation)', async () => {
    const backend = new NullBackend();
    await executeAction(action('drag', { x1: 5, y1: 6, x2: 7, y2: 8 }), backend);
    expect(backend.calls).toEqual([{ method: 'drag', args: [5, 6, 7, 8] }]);
  });

  it('throws when endpoints are missing', async () => {
    await expect(
      executeAction(action('drag', { from_x: 1, from_y: 2 }), new NullBackend()),
    ).rejects.toThrow(ActionParamError);
  });
});

describe('move / type_text', () => {
  it('moves the pointer', async () => {
    const backend = new NullBackend();
    await executeAction(action('move', { x: 9, y: 8 }), backend);
    expect(backend.calls).toEqual([{ method: 'move', args: [9, 8] }]);
  });

  it('types text (preserving it exactly)', async () => {
    const backend = new NullBackend();
    await executeAction(action('type_text', { text: 'héllo  world!' }), backend);
    expect(backend.calls).toEqual([{ method: 'typeText', args: ['héllo  world!'] }]);
  });

  it('throws on a missing text param', async () => {
    await expect(executeAction(action('type_text'), new NullBackend())).rejects.toThrow(
      ActionParamError,
    );
  });
});

describe('coordinate scaling', () => {
  it('scales click/drag coordinates by (real / sent) and rounds', async () => {
    const backend = new NullBackend();
    // Sent a 1280x720 screenshot of a 1920x1080 screen -> scale 1.5x.
    const options = { scaleX: 1.5, scaleY: 1.5 };
    await executeAction(action('click', { x: 100, y: 33 }), backend, options);
    await executeAction(
      action('drag', { from_x: 10, from_y: 20, to_x: 30, to_y: 41 }),
      backend,
      options,
    );
    expect(backend.calls[0]?.args.slice(0, 2)).toEqual([150, 50]); // 33*1.5 = 49.5 -> 50
    expect(backend.calls[1]?.args).toEqual([15, 30, 45, 62]); // 41*1.5 = 61.5 -> 62
  });

  it('scales the optional scroll position but never the amount', async () => {
    const backend = new NullBackend();
    await executeAction(
      action('scroll', { direction: 'down', amount: 3, x: 100, y: 100 }),
      backend,
      { scaleX: 2, scaleY: 0.5 },
    );
    expect(backend.calls[0]?.args[0]).toEqual({ direction: 'down', amount: 3, x: 200, y: 50 });
  });
});

describe('raw — never executed', () => {
  it('logs and skips raw actions instead of executing them', async () => {
    const backend = new NullBackend();
    const logged: string[] = [];
    const result = await executeAction(
      action('raw', { code: 'pyautogui.hotkey("ctrl", "alt", "del")' }),
      backend,
      { logger: (message) => logged.push(message) },
    );

    expect(result.executed).toBe(false);
    expect(result.terminal).toBeNull();
    expect(result.detail).toContain('skipped raw action');
    expect(backend.calls).toEqual([]); // backend NEVER touched
    expect(logged).toHaveLength(1);
    expect(logged[0]).toContain('pyautogui.hotkey');
  });

  it('falls back to action.raw_code for the log preview', async () => {
    const logged: string[] = [];
    await executeAction(
      { action_type: 'raw', params: {}, raw_code: 'pyautogui.moveTo(1, 2)' },
      new NullBackend(),
      { logger: (message) => logged.push(message) },
    );
    expect(logged[0]).toContain('pyautogui.moveTo(1, 2)');
  });
});

describe('terminal markers', () => {
  it('done is terminal and executes nothing', async () => {
    const backend = new NullBackend();
    const result = await executeAction(action('done'), backend);
    expect(result).toEqual({ actionType: 'done', executed: false, terminal: 'done', detail: null });
    expect(backend.calls).toEqual([]);
  });

  it('fail is terminal and carries the reason', async () => {
    const result = await executeAction(
      action('fail', { reason: 'element not found' }),
      new NullBackend(),
    );
    expect(result.terminal).toBe('fail');
    expect(result.detail).toBe('element not found');
  });

  it('unknown action types throw ActionParamError', async () => {
    await expect(
      executeAction(
        { action_type: 'levitate' as Action['action_type'], params: {} },
        new NullBackend(),
      ),
    ).rejects.toThrow(ActionParamError);
  });
});

describe('executeActions', () => {
  it('executes in order and stops at the first terminal marker', async () => {
    const backend = new NullBackend();
    const results = await executeActions(
      [
        action('click', { x: 1, y: 2 }),
        action('type_text', { text: 'hi' }),
        action('done'),
        action('click', { x: 9, y: 9 }), // must NOT run
      ],
      backend,
    );

    expect(results).toHaveLength(3);
    expect(results[2]?.terminal).toBe('done');
    expect(backend.calls.map((c) => c.method)).toEqual(['click', 'typeText']);
  });
});
