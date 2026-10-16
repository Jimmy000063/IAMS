import '@testing-library/jest-dom/vitest';

import { afterEach, beforeEach } from 'vitest';

// Each test starts with no stored session, so one test signing in cannot leave
// another one unexpectedly authenticated.
beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  sessionStorage.clear();
});
