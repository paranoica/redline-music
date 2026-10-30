// The song page: mounts the app on the page body.

import { token } from "../core/token.ts";
import { mount } from "./main.ts";

void mount(document.body, { base: "", token: token() });
