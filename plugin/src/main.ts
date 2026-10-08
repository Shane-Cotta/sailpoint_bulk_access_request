import { bootstrapApplication } from '@angular/platform-browser';

import { App } from './app/app';
import { appConfig } from './app/app.config';
import { demoScenario } from './app/demo/scenario';

// ?demo=<scenario> renders the page standalone with fixture data (for screenshots and
// UI work without a tenant). Ignored inside ISC, where the page always runs in an iframe.
// The demo code and fixtures load only then, as a separate chunk.
const scenario = demoScenario(window.location);
const extra = scenario ? import('./app/demo/demo').then((demo) => demo.demoProviders(scenario)) : Promise.resolve([]);

extra
  .then((providers) => bootstrapApplication(App, appConfig(providers)))
  .catch((err) => console.error(err));
