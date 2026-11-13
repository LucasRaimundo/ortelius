// Flow "Validar publicação (entidade existente)". Devolve { block: true } quando a alteração deixa alguma
// entidade publicada sem fontes. `current_citations` vem da operação anterior, que lê as citações já salvas.
// A garantia real é o trigger do banco; este Flow só troca o erro técnico por uma validação legível.
module.exports = async function (data) {
	const payload = data.$trigger.payload;
	if (payload.status !== 'published') return { block: false };

	const citations = payload.citations;
	if (Array.isArray(citations)) return { block: citations.length === 0 };

	const added = citations?.create?.length ?? 0;
	const removed = citations?.delete?.length ?? 0;
	const current = data.current_citations ?? [];
	const block = data.$trigger.keys.some(
		(key) => current.filter((row) => row.entity_id === key).length + added - removed < 1,
	);
	return { block };
};
