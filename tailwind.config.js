/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./mcpgateway/templates/**/*.html",
        "./mcpgateway/static/**/*.js",
    ],
    darkMode: "class",
    safelist: [
        // Verdict / severity left-border colors (Jinja if/elif + JS optimistic reflect)
        "border-green-500", "border-amber-400", "border-sky-400", "border-gray-300", "border-red-500",
        // Ahead/behind divergence numerals
        "text-green-600", "dark:text-green-400",
        "text-amber-600", "dark:text-amber-400",
        "text-red-600", "dark:text-red-400",
        "text-gray-400", "dark:text-gray-500",
        // Running-row pulse
        "animate-pulse-soft",
    ],
    theme: {
        extend: {
            animation: {
                float: "float 6s ease-in-out infinite",
                "pulse-soft": "pulse-soft 2s ease-in-out infinite",
                "slide-up": "slide-up 0.8s ease-out",
                "fade-in": "fade-in 1s ease-out",
            },
            keyframes: {
                float: {
                    "0%, 100%": { transform: "translateY(0px)" },
                    "50%": { transform: "translateY(-20px)" },
                },
                "pulse-soft": {
                    "0%, 100%": { opacity: "1" },
                    "50%": { opacity: "0.8" },
                },
                "slide-up": {
                    "0%": { transform: "translateY(30px)", opacity: "0" },
                    "100%": { transform: "translateY(0)", opacity: "1" },
                },
                "fade-in": {
                    "0%": { opacity: "0" },
                    "100%": { opacity: "1" },
                },
            },
        },
    },
    plugins: [],
};
