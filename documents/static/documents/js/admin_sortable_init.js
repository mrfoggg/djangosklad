document.addEventListener("DOMContentLoaded", function () {
	// const isApplied = document.getElementById("id_is_applied").checked;
	// if (isApplied) return;

	const inlinesTable = document.querySelector("#items-data, #paymentoutitem_set-data");
	if (!inlinesTable || typeof Sortable === "undefined") return;

	const injectDragHandles = () => {
		const sortInputs = inlinesTable.querySelectorAll('input[id*="sort_order"]');

		sortInputs.forEach((input) => {
			// Если в этой ячейке уже есть ручка, пропускаем
			if (input.parentNode.querySelector(".drag-handler")) return;

			// Создаем ручку
			const handle = document.createElement("div");
			handle.className = "drag-handler cursor-move text-gray-400 mr-2 flex items-center justify-center";
			handle.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="9" cy="12" r="1"/><circle cx="9" cy="5" r="1"/><circle cx="9" cy="19" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="19" r="1"/>
                </svg>`;

			// Прячем текстовое поле и ставим ручку в начало ячейки
			input.type = "hidden";
			input.parentNode.prepend(handle);
		});
	};

	// 1. Инициализация при загрузке
	injectDragHandles();

	const updateOrder = () => {
		Array.from(inlinesTable.children)
			.filter((row) => row.matches("tbody:not(.empty-form)"))
			.forEach((row, index) => {
				const input = row.querySelector('input[id*="sort_order"]');
				const invoice = row.querySelector('select[name$="-invoice"]');
				// Do not turn the unused extra payment row into a changed form.
				if (input && (!invoice || invoice.value)) input.value = index;
			});
	};

	Sortable.create(inlinesTable, {
		handle: ".drag-handler",
		draggable: "tbody:not(.empty-form)",
		animation: 150,
		onEnd: updateOrder,
	});
	if (inlinesTable.id === "paymentoutitem_set-data") {
		inlinesTable.closest("form")?.addEventListener("submit", updateOrder);
	}
	document.addEventListener("formset:added", injectDragHandles);
});
