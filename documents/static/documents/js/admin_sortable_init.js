document.addEventListener("DOMContentLoaded", function () {
	// const isApplied = document.getElementById("id_is_applied").checked;
	// if (isApplied) return;

	const inlinesTable = document.querySelector("#items-data");
	if (!inlinesTable) return;

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

	// 2. Настройка SortableJS
	// В Unfold #items-data — это обычно контейнер строк (tbody или обертка)
	Sortable.create(inlinesTable, {
		handle: ".drag-handler",
		animation: 150,
		onEnd: function () {
			// Пересчитываем индексы для всех полей в колонке
			inlinesTable.querySelectorAll("tbody:not(.template)").forEach((row, index) => {
				const input = row.querySelector('input[id*="sort_order"]');
				// console.log("input: ", input, "index: ", index);
				if (input) {
					input.value = index;
					console.log(`New index for row ${index}: ${input.value}`);
				}
			});
		},
	});
});
