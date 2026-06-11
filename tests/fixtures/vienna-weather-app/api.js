/**
 * Vienna weather client — Open-Meteo (no API key).
 * Exposed globally as WeatherAPI for plain <script> tags.
 */
(function (global) {
  "use strict";

  const VIENNA_LAT = 48.2082;
  const VIENNA_LON = 16.3738;
  const TIMEZONE = "Europe/Vienna";
  const BASE_URL = "https://api.open-meteo.com/v1/forecast";

  /** WMO weather interpretation codes → description + emoji hint. */
  const WMO_CODES = {
    0: { description: "Clear sky", emoji: "☀️" },
    1: { description: "Mainly clear", emoji: "🌤️" },
    2: { description: "Partly cloudy", emoji: "⛅" },
    3: { description: "Overcast", emoji: "☁️" },
    45: { description: "Fog", emoji: "🌫️" },
    48: { description: "Depositing rime fog", emoji: "🌫️" },
    51: { description: "Light drizzle", emoji: "🌦️" },
    53: { description: "Moderate drizzle", emoji: "🌦️" },
    55: { description: "Dense drizzle", emoji: "🌧️" },
    56: { description: "Light freezing drizzle", emoji: "🌧️" },
    57: { description: "Dense freezing drizzle", emoji: "🌧️" },
    61: { description: "Slight rain", emoji: "🌧️" },
    63: { description: "Moderate rain", emoji: "🌧️" },
    65: { description: "Heavy rain", emoji: "🌧️" },
    66: { description: "Light freezing rain", emoji: "🌨️" },
    67: { description: "Heavy freezing rain", emoji: "🌨️" },
    71: { description: "Slight snow fall", emoji: "🌨️" },
    73: { description: "Moderate snow fall", emoji: "❄️" },
    75: { description: "Heavy snow fall", emoji: "❄️" },
    77: { description: "Snow grains", emoji: "🌨️" },
    80: { description: "Slight rain showers", emoji: "🌦️" },
    81: { description: "Moderate rain showers", emoji: "🌧️" },
    82: { description: "Violent rain showers", emoji: "⛈️" },
    85: { description: "Slight snow showers", emoji: "🌨️" },
    86: { description: "Heavy snow showers", emoji: "❄️" },
    95: { description: "Thunderstorm", emoji: "⛈️" },
    96: { description: "Thunderstorm with slight hail", emoji: "⛈️" },
    99: { description: "Thunderstorm with heavy hail", emoji: "⛈️" },
  };

  /**
   * @param {number} code
   * @returns {{ code: number, description: string, emoji: string }}
   */
  function mapWeatherCode(code) {
    const entry = WMO_CODES[code];
    if (entry) {
      return { code, description: entry.description, emoji: entry.emoji };
    }
    return { code, description: "Unknown conditions", emoji: "❓" };
  }

  /**
   * @param {Record<string, unknown>} current
   */
  function normalizeCurrent(current) {
    const weatherCode = Number(current.weather_code);
    const weather = mapWeatherCode(weatherCode);

    return {
      temperatureC: Number(current.temperature_2m),
      apparentTemperatureC: Number(current.apparent_temperature),
      humidityPercent: Number(current.relative_humidity_2m),
      windSpeedKmh: Number(current.wind_speed_10m),
      weatherCode,
      description: weather.description,
      emoji: weather.emoji,
      fetchedAt: new Date().toISOString(),
    };
  }

  /**
   * @param {string} [message]
   * @returns {{ ok: false, error: string }}
   */
  function fail(message) {
    return { ok: false, error: message || "Unknown error" };
  }

  /**
   * @template T
   * @param {T} data
   * @returns {{ ok: true, data: T }}
   */
  function succeed(data) {
    return { ok: true, data };
  }

  /**
   * Build the Open-Meteo forecast URL for Vienna current conditions.
   * @returns {string}
   */
  function buildForecastUrl() {
    const params = new URLSearchParams({
      latitude: String(VIENNA_LAT),
      longitude: String(VIENNA_LON),
      current:
        "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature",
      timezone: TIMEZONE,
    });
    return `${BASE_URL}?${params.toString()}`;
  }

  /**
   * Fetch current weather for Vienna, Austria.
   * @returns {Promise<{ ok: boolean, data?: object, error?: string }>}
   */
  async function fetchCurrentWeather() {
    const url = buildForecastUrl();

    let response;
    try {
      response = await fetch(url);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Network request failed";
      return fail(`Failed to reach weather service: ${message}`);
    }

    if (!response.ok) {
      return fail(
        `Weather service returned HTTP ${response.status} ${response.statusText}`
      );
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      return fail("Invalid JSON response from weather service");
    }

    const current = payload && payload.current;
    if (!current || typeof current !== "object") {
      return fail("Weather service response missing current conditions");
    }

    try {
      return succeed(normalizeCurrent(current));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to parse weather data";
      return fail(message);
    }
  }

  const WeatherAPI = {
    VIENNA_LAT,
    VIENNA_LON,
    TIMEZONE,
    mapWeatherCode,
    buildForecastUrl,
    fetchCurrentWeather,
  };

  global.WeatherAPI = WeatherAPI;
})(typeof globalThis !== "undefined" ? globalThis : window);
