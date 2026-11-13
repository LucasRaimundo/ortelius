// Flow "Validar publicação (nova entidade)". Devolve { block: true } quando uma entidade nasce publicada sem
// fontes. A garantia real é o trigger do banco; este Flow só troca o erro técnico por uma validação legível.
module.exports = async function (data) {
	const payload = data.$trigger.payload;
	if (payload.status !== 'published') return { block: false };

	// A API aceita lista; o painel envia { create, update, delete }.
	const citations = payload.citations;
	const added = Array.isArray(citations) ? citations.length : (citations?.create?.length ?? 0);
	return { block: added === 0 };
};
