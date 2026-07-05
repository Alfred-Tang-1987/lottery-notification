// Vitest setup file for jsdom environment.
// Provides localStorage and matchMedia when the built-in jsdom globals are missing.

const noopStorage = {
  getItem: () => null,
  setItem: () => undefined,
  removeItem: () => undefined,
  clear: () => undefined,
  key: () => null,
  length: 0,
};

if (typeof localStorage === "undefined") {
  Object.defineProperty(globalThis, "localStorage", {
    value: noopStorage,
    writable: true,
  });
}

if (typeof window !== "undefined" && !window.localStorage) {
  Object.defineProperty(window, "localStorage", {
    value: noopStorage,
    writable: true,
  });
}

if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    value: () => ({
      matches: false,
      media: "",
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => true,
    }),
    writable: true,
  });
}
