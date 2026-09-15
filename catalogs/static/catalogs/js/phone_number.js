(() => {
    "use strict";

    function setup(input) {
        if (input.dataset.phoneMaskReady) return;
        const country = document.getElementById(input.id.replace(/_1$/, "_0"));
        if (!country) return;
        input.dataset.phoneMaskReady = "true";

        function format(complete = false) {
            if (country.value !== "UA") return;
            const original = input.value;
            // Leave unexpected characters for server validation rather than silently discarding them.
            if (!/^[\d\s()+-]*$/.test(original)) return;
            const caret = input.selectionStart;
            let before = original.slice(0, caret ?? original.length).replace(/\D/g, "").length;
            let digits = original.replace(/\D/g, "");
            if (digits.startsWith("380") && digits.length === 12) {
                digits = digits.slice(2);
                before = Math.max(0, before - 2);
            } else if (complete && digits.length === 9 && !digits.startsWith("0")) {
                digits = "0" + digits;
                before += 1;
            }
            // Do not truncate extra digits: invalid numbers must remain visible.
            const formatted = [digits.slice(0, 3), digits.slice(3, 6), digits.slice(6)].filter(Boolean).join(" ");
            if (formatted === original) return;
            input.value = formatted;
            let position = 0;
            let count = 0;
            while (position < formatted.length && count < before) {
                if (/\d/.test(formatted[position])) count += 1;
                position += 1;
            }
            if (document.activeElement === input) input.setSelectionRange(position, position);
        }

        function updateCountry() {
            input.placeholder = country.value === "UA" ? "050 123 4567" : "";
            format(true);
        }
        input.addEventListener("input", () => format());
        input.addEventListener("blur", () => format(true));
        country.addEventListener("change", updateCountry);
        updateCountry();
    }

    function init() {
        document.querySelectorAll("input[data-phone-country-mask]").forEach(setup);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
    document.addEventListener("formset:added", init);
})();
