import Modal from './Modal';
import Button from './Button';

export default function ConfirmModal({ open, onClose, onCancel, onConfirm, title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', variant = 'danger', loading = false }) {
    const handleClose = onCancel || onClose;
    return (
        <Modal open={open} onClose={handleClose} title={title}>
            <div className="space-y-4">
                <p className="text-slate-600 dark:text-slate-400 text-sm">{message}</p>
                <div className="flex gap-2 justify-end">
                    <Button variant="secondary" onClick={handleClose} disabled={loading}>
                        {cancelLabel}
                    </Button>
                    <Button variant={variant} onClick={onConfirm} disabled={loading}>
                        {loading ? '...' : confirmLabel}
                    </Button>
                </div>
            </div>
        </Modal>
    );
}
