import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import de from "./de.json";
import en from "./en.json";
import fr from "./fr.json";
import it from "./it.json";

export const LOCALES = ["en", "de", "fr", "it"] as const;

const stored = (() => {
  try {
    return localStorage.getItem("dpac.locale");
  } catch {
    return null;
  }
})();

i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, de: { translation: de }, fr: { translation: fr }, it: { translation: it } },
  lng: stored ?? (navigator.language?.slice(0, 2) || "en"),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
