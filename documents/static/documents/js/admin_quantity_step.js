function formatQuantityForPrecision(input, decimalPlaces) {
	const match = input.value.replace(",", ".").match(/^(-?\d+)(?:\.(\d+))?$/);
	if (!match) {
		return;
	}

	const [, integerPart, fraction = ""] = match;
	const discardedPart = fraction.slice(decimalPlaces);
	if (discardedPart && !/^0+$/.test(discardedPart)) {
		return;
	}

	input.value =
		decimalPlaces === 0
			? integerPart
			: `${integerPart}.${fraction.padEnd(decimalPlaces, "0").slice(0, decimalPlaces)}`;
}

function applyProductQuantityPrecision(productSelect) {
	const row = productSelect?.closest("tr, .inline-related");
	const quantityInput = row?.querySelector('input[id$="-quantity"]');
	const selectedOption = productSelect?.selectedOptions?.[0];
	if (!quantityInput) {
		return;
	}

	const parsedDecimalPlaces = Number.parseInt(
		selectedOption?.dataset.quantityDecimalPlaces ?? "0",
		10,
	);
	const decimalPlaces = Number.isInteger(parsedDecimalPlaces)
		? Math.max(0, parsedDecimalPlaces)
		: 0;

	quantityInput.step =
		decimalPlaces === 0 ? "1" : `0.${"0".repeat(decimalPlaces - 1)}1`;
	quantityInput.dataset.unitSymbol = selectedOption?.dataset.unitSymbol ?? "";
	formatQuantityForPrecision(quantityInput, decimalPlaces);
}

function initializeProductQuantityPrecision(root = document) {
	root.querySelectorAll?.('select[id$="-product"]').forEach(applyProductQuantityPrecision);
}

document.addEventListener("change", (event) => {
	if (event.target?.matches('select[id$="-product"]')) {
		applyProductQuantityPrecision(event.target);
	}
});

document.addEventListener("formset:added", (event) => {
	initializeProductQuantityPrecision(event.target);
});

const quantityFormsetObserver = new MutationObserver((mutations) => {
	for (const mutation of mutations) {
		for (const node of mutation.addedNodes) {
			if (node.nodeType === Node.ELEMENT_NODE) {
				initializeProductQuantityPrecision(node);
			}
		}
	}
});

function startProductQuantityPrecision() {
	initializeProductQuantityPrecision();
	quantityFormsetObserver.observe(document.body, { childList: true, subtree: true });
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", startProductQuantityPrecision);
} else {
	startProductQuantityPrecision();
}
