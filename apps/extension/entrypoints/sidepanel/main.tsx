import { render } from "preact";
import { SettingsApp } from "../../components/SettingsApp";
import "../../components/settings.css";

const root = document.getElementById("app");
if (root) render(<SettingsApp />, root);
