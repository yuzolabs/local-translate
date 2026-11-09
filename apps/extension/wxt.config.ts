import { defineConfig } from "wxt";
import preact from "@preact/preset-vite";

const manifestKey =
  "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAq5sjffKr4FduQXbZiJeHn7jd/BDU8xelMkg9lrF+76ypMHRr/WZQJ0KzbndpHTgxTjz8reFutzYuYas0fmoSKJ/ojiDf+IbtUagzhWs3r1hgopjnYbNQxMy2X5iEEfyrnyo3D4aUSGFGFS6a0Bh9TTPx7cJ2s6OpkY0XksqOirCPnegKp4JwAJr3QVHu9VT0ms94TSbLQa7tAbD423nOUlZsvdOvi3cGkttsO24wTJm9HrPOXEwkWtY3vYL8APGdGHMQdRZYNRPs+DFkGkuDykM1iNU45v96+3hI1SEqGA2W8Ru/DfBU3nfGmsxJrtcY2xcmVCe4M5PZ2bWeR+r8wwIDAQAB";

export default defineConfig({
  srcDir: ".",
  manifest: {
    name: "ローカル翻訳",
    description: "ページ、選択範囲、入力欄、動画字幕を端末内で翻訳する",
    key: manifestKey,
    permissions: [
      "nativeMessaging",
      "tabCapture",
      "offscreen",
      "contextMenus",
      "storage",
      "scripting",
      "sidePanel",
      "activeTab",
      "tabs",
    ],
    host_permissions: ["http://127.0.0.1/*", "http://localhost/*"],
    action: {
      default_title: "ローカル翻訳",
      default_icon: {
        16: "icon-16.png",
        32: "icon-32.png",
        48: "icon-48.png",
        128: "icon-128.png",
      },
    },
    icons: {
      16: "icon-16.png",
      32: "icon-32.png",
      48: "icon-48.png",
      128: "icon-128.png",
    },
    commands: {
      "translate-selection": {
        suggested_key: { default: "Alt+Shift+Y" },
        description: "選択範囲を翻訳",
      },
      "translate-input": {
        suggested_key: { default: "Alt+A" },
        description: "入力欄を翻訳",
      },
    },
  },
  vite: () => ({
    plugins: [preact()],
  }),
});
