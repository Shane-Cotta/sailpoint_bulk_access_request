import { bootstrapApplication } from '@angular/platform-browser';

import { App } from './app/app';
import { appConfig } from './app/app.config';
import { demoProviders, demoScenario } from './app/demo/demo';

// ?demo=<scenario> renders the page standalone with fixture data (for screenshots and
// UI work without a tenant). Ignored inside ISC, where the page always runs in an iframe.
const scenario = demoScenario(window.location);

bootstrapApplication(App, appConfig(scenario ? demoProviders(scenario) : []))
  .catch((err) => console.error(err));
