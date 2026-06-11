/**
 * Vienna weather dashboard — UI controller.
 * Depends on global WeatherAPI from api.js.
 */
(function () {
  "use strict";

  const CARD_STATE_CLASSES = [
    "weather-card--loading",
    "weather-card--ready",
    "weather-card--error",
  ];

  /** @type {HTMLElement | null} */
  let card = null;

  /**
   * @param {string} id
   * @returns {HTMLElement | null}
   */
  function $(id) {
    return document.getElementById(id);
  }

  /**
   * @param {number} celsius
   * @returns {string}
   */
  function formatTemp(celsius) {
    return String(Math.round(celsius));
  }

  /**
   * @param {number} kmh
   * @returns {string}
   */
  function formatWind(kmh) {
    return Number(kmh).toFixed(1);
  }

  /**
   * @param {number} percent
   * @returns {string}
   */
  function formatHumidity(percent) {
    return String(Math.round(percent));
  }

  /**
   * @param {string} isoString
   * @returns {string}
   */
  function formatUpdated(isoString) {
    const date = new Date(isoString);
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Vienna",
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }

  /**
   * @param {"loading" | "ready" | "error"} state
   */
  function setCardState(state) {
    if (!card) {
      return;
    }

    CARD_STATE_CLASSES.forEach((cls) => card.classList.remove(cls));
    card.classList.add(`weather-card--${state}`);
    card.setAttribute("aria-busy", state === "loading" ? "true" : "false");
  }

  /**
   * @param {{
   *   temperatureC: number,
   *   apparentTemperatureC: number,
   *   humidityPercent: number,
   *   windSpeedKmh: number,
   *   description: string,
   *   emoji: string,
   *   fetchedAt: string,
   * }} data
   */
  function populateWeather(data) {
    const temperatureEl = $("weather-temperature");
    const iconEl = $("weather-icon");
    const conditionEl = $("weather-condition");
    const humidityEl = $("weather-humidity");
    const windEl = $("weather-wind");
    const feelsLikeEl = $("weather-feels-like");
    const updatedEl = $("weather-updated");

    if (temperatureEl) {
      temperatureEl.textContent = formatTemp(data.temperatureC);
    }

    if (iconEl) {
      const emojiSpan = iconEl.querySelector("span");
      if (emojiSpan) {
        emojiSpan.textContent = data.emoji;
      } else {
        iconEl.textContent = data.emoji;
      }
      iconEl.setAttribute("aria-label", data.description);
    }

    if (conditionEl) {
      conditionEl.textContent = data.description;
    }

    if (humidityEl) {
      humidityEl.textContent = formatHumidity(data.humidityPercent);
    }

    if (windEl) {
      windEl.textContent = formatWind(data.windSpeedKmh);
    }

    if (feelsLikeEl) {
      feelsLikeEl.textContent = formatTemp(data.apparentTemperatureC);
    }

    if (updatedEl) {
      updatedEl.dateTime = data.fetchedAt;
      updatedEl.textContent = `Updated ${formatUpdated(data.fetchedAt)}`;
    }
  }

  /**
   * @param {string} [message]
   */
  function showError(message) {
    const errorMessageEl = $("weather-error-message");
    if (errorMessageEl) {
      errorMessageEl.textContent =
        message ||
        "Something went wrong while fetching the latest conditions.";
    }
    setCardState("error");
  }

  async function loadWeather() {
    if (
      typeof WeatherAPI === "undefined" ||
      typeof WeatherAPI.fetchCurrentWeather !== "function"
    ) {
      showError("Weather API is not available.");
      return;
    }

    setCardState("loading");

    const result = await WeatherAPI.fetchCurrentWeather();

    if (!result.ok || !result.data) {
      showError(result.error || "Failed to load weather data.");
      return;
    }

    populateWeather(result.data);
    setCardState("ready");
  }

  function init() {
    card = document.querySelector(".weather-card");

    const retryBtn = $("weather-retry");
    if (retryBtn) {
      retryBtn.addEventListener("click", loadWeather);
    }

    loadWeather();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
