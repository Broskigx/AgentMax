import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type ReactNode,
} from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import {
  ArrowUp,
  BrainCog,
  ImagePlus,
  MonitorUp,
  Paperclip,
  Square,
  X,
} from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import type { ChatAttachment } from '@/types';

export type PromptMode = 'chat' | 'think' | 'computer';

export interface PromptInputBoxHandle {
  focus: () => void;
}

interface PromptInputBoxProps {
  value: string;
  onValueChange: (value: string) => void;
  onSend: (message: string, attachments: ChatAttachment[], mode: PromptMode) => void | Promise<void>;
  onStop?: () => void | Promise<void>;
  isLoading?: boolean;
  disabled?: boolean;
  supportsVision?: boolean;
  placeholder?: string;
  className?: string;
}

const MAX_ATTACHMENTS = 4;
const MAX_FILE_SIZE = 10 * 1024 * 1024;

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('No se pudo leer la imagen.'));
    reader.onload = () => resolve(String(reader.result || ''));
    reader.readAsDataURL(file);
  });
}

function readImageSize(dataUrl: string) {
  return new Promise<{ width?: number; height?: number }>((resolve) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => resolve({});
    image.src = dataUrl;
  });
}

function TooltipButton({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <TooltipPrimitive.Root delayDuration={280}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side="top"
          sideOffset={8}
          className="z-[10000] rounded-lg border border-white/10 bg-[#18181a] px-2.5 py-1.5 text-[11px] font-medium text-zinc-200 shadow-xl"
        >
          {label}
          <TooltipPrimitive.Arrow className="fill-[#18181a]" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export const PromptInputBox = forwardRef<PromptInputBoxHandle, PromptInputBoxProps>(
  (
    {
      value,
      onValueChange,
      onSend,
      onStop,
      isLoading = false,
      disabled = false,
      supportsVision = true,
      placeholder = 'Escribe una instrucción para AgentMax...',
      className,
    },
    ref,
  ) => {
    const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
    const [selectedImage, setSelectedImage] = useState<ChatAttachment | null>(null);
    const [mode, setMode] = useState<PromptMode>('chat');
    const [dragging, setDragging] = useState(false);
    const [error, setError] = useState('');
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const uploadInputRef = useRef<HTMLInputElement>(null);

    useImperativeHandle(ref, () => ({
      focus: () => textareaRef.current?.focus(),
    }));

    useEffect(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
    }, [value]);

    const processFiles = useCallback(async (files: File[]) => {
      setError('');
      if (!supportsVision) {
        setError('El modelo activo no informa soporte de visión.');
        return;
      }

      const available = MAX_ATTACHMENTS - attachments.length;
      const candidates = files.slice(0, available);
      if (candidates.length === 0) {
        setError(`Puedes adjuntar hasta ${MAX_ATTACHMENTS} imágenes.`);
        return;
      }

      const next: ChatAttachment[] = [];
      for (const file of candidates) {
        if (!file.type.startsWith('image/')) {
          setError('AgentMax acepta imágenes en este compositor.');
          continue;
        }
        if (file.size > MAX_FILE_SIZE) {
          setError(`${file.name} supera el límite de 10 MB.`);
          continue;
        }
        const dataUrl = await readFileAsDataUrl(file);
        const dimensions = await readImageSize(dataUrl);
        next.push({
          id: globalThis.crypto?.randomUUID?.() || `img-${Date.now()}-${next.length}`,
          kind: 'image',
          name: file.name,
          mime: file.type,
          size: file.size,
          dataUrl,
          ...dimensions,
        });
      }

      if (next.length) setAttachments((current) => [...current, ...next].slice(0, MAX_ATTACHMENTS));
    }, [attachments.length, supportsVision]);

    const handlePaste = useCallback((event: ClipboardEvent<HTMLDivElement>) => {
      const files = Array.from(event.clipboardData.files);
      if (!files.some((file) => file.type.startsWith('image/'))) return;
      event.preventDefault();
      void processFiles(files);
    }, [processFiles]);

    const handleDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setDragging(false);
      void processFiles(Array.from(event.dataTransfer.files));
    }, [processFiles]);

    const canSend = Boolean(value.trim() || attachments.length);
    const isDisabled = disabled || isLoading;

    const submit = useCallback(() => {
      if (!canSend || isDisabled) return;
      const message = value.trim();
      const files = attachments;
      onValueChange('');
      setAttachments([]);
      setError('');
      void onSend(message, files, mode);
    }, [attachments, canSend, isDisabled, mode, onSend, onValueChange, value]);

    const modePlaceholder =
      mode === 'think'
        ? 'Pide un análisis profundo...'
        : mode === 'computer'
          ? 'Describe la acción de escritorio; AgentMax pedirá permiso...'
          : placeholder;

    return (
      <TooltipPrimitive.Provider>
        <div
          className={cn(
            'relative rounded-[22px] border border-white/10 bg-[#161618]/95 p-2 shadow-[0_16px_44px_rgba(0,0,0,0.34)] transition duration-200',
            'focus-within:border-orange-400/35 focus-within:shadow-[0_16px_44px_rgba(0,0,0,0.38),0_0_0_3px_rgba(249,115,22,0.06)]',
            dragging && 'border-orange-400/60 bg-orange-400/[0.06]',
            isLoading && 'border-orange-400/30',
            className,
          )}
          onPaste={handlePaste}
          onDragEnter={(event) => {
            event.preventDefault();
            if (supportsVision) setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
          }}
          onDrop={handleDrop}
        >
          <AnimatePresence initial={false}>
            {attachments.length ? (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="flex gap-2 overflow-x-auto px-1 pb-2"
              >
                {attachments.map((attachment) => (
                  <div key={attachment.id} className="group relative h-16 w-16 shrink-0">
                    <button
                      type="button"
                      onClick={() => setSelectedImage(attachment)}
                      className="h-full w-full overflow-hidden rounded-xl border border-white/10 bg-black/40"
                      aria-label={`Ver ${attachment.name}`}
                    >
                      <img
                        src={attachment.dataUrl}
                        alt=""
                        className="h-full w-full object-cover transition duration-200 group-hover:scale-105"
                      />
                    </button>
                    <button
                      type="button"
                      onClick={() => setAttachments((current) => current.filter((item) => item.id !== attachment.id))}
                      className="absolute -right-1 -top-1 grid h-5 w-5 place-items-center rounded-full border border-white/15 bg-black text-zinc-300 shadow-lg hover:text-white"
                      aria-label={`Quitar ${attachment.name}`}
                    >
                      <X size={11} />
                    </button>
                  </div>
                ))}
              </motion.div>
            ) : null}
          </AnimatePresence>

          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={isLoading ? 'AgentMax está trabajando...' : modePlaceholder}
            rows={1}
            disabled={isDisabled}
            className="block min-h-12 w-full resize-none overflow-y-auto border-0 bg-transparent px-3 py-3 text-[13px] leading-6 text-zinc-100 outline-none placeholder:text-zinc-600 disabled:cursor-not-allowed disabled:opacity-55"
          />

          {error ? (
            <div className="mx-2 mb-2 rounded-lg border border-red-400/15 bg-red-400/[0.07] px-3 py-2 text-[11px] text-red-300">
              {error}
            </div>
          ) : null}

          <div className="flex min-w-0 items-center justify-between gap-2 px-1 pb-1">
            <div className="flex min-w-0 items-center gap-1">
              <TooltipButton label={supportsVision ? 'Adjuntar imágenes' : 'Visión no disponible'}>
                <button
                  type="button"
                  onClick={() => uploadInputRef.current?.click()}
                  disabled={isDisabled || !supportsVision}
                  aria-label={supportsVision ? 'Adjuntar imágenes' : 'Visión no disponible'}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-transparent text-zinc-500 transition hover:border-white/[0.07] hover:bg-white/[0.05] hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  <Paperclip size={17} />
                </button>
              </TooltipButton>
              <input
                ref={uploadInputRef}
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={(event) => {
                  if (event.target.files) void processFiles(Array.from(event.target.files));
                  event.target.value = '';
                }}
              />

              <div className="mx-1 h-5 w-px bg-white/[0.08]" />

              <TooltipButton label="Chat normal">
                <button
                  type="button"
                  onClick={() => setMode('chat')}
                  aria-pressed={mode === 'chat'}
                  className={cn(
                    'flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[11px] font-semibold transition',
                    mode === 'chat'
                      ? 'border-orange-400/25 bg-orange-400/10 text-orange-300'
                      : 'border-transparent text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200',
                  )}
                >
                  <ImagePlus size={15} />
                  <span className="hidden sm:inline">Chat</span>
                </button>
              </TooltipButton>

              <TooltipButton label="Solicitar análisis profundo">
                <button
                  type="button"
                  onClick={() => setMode((current) => current === 'think' ? 'chat' : 'think')}
                  aria-pressed={mode === 'think'}
                  className={cn(
                    'flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[11px] font-semibold transition',
                    mode === 'think'
                      ? 'border-violet-400/30 bg-violet-400/10 text-violet-300'
                      : 'border-transparent text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200',
                  )}
                >
                  <BrainCog size={15} />
                  <span className="hidden sm:inline">Pensar</span>
                </button>
              </TooltipButton>

              <TooltipButton label="Usar ComputerMax con permiso explícito">
                <button
                  type="button"
                  onClick={() => setMode((current) => current === 'computer' ? 'chat' : 'computer')}
                  aria-pressed={mode === 'computer'}
                  className={cn(
                    'flex h-9 items-center gap-1.5 rounded-xl border px-2.5 text-[11px] font-semibold transition',
                    mode === 'computer'
                      ? 'border-sky-400/30 bg-sky-400/10 text-sky-300'
                      : 'border-transparent text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200',
                  )}
                >
                  <MonitorUp size={15} />
                  <span className="hidden sm:inline">ComputerMax</span>
                </button>
              </TooltipButton>
            </div>

            <TooltipButton label={isLoading ? 'Detener respuesta' : canSend ? 'Enviar' : 'Escribe un mensaje'}>
              <button
                type="button"
                onClick={() => {
                  if (isLoading) void onStop?.();
                  else submit();
                }}
                disabled={!isLoading && (!canSend || disabled)}
                aria-label={isLoading ? 'Detener respuesta' : 'Enviar mensaje'}
                className={cn(
                  'grid h-9 w-9 shrink-0 place-items-center rounded-xl border transition',
                  isLoading
                    ? 'border-red-400/25 bg-red-400/10 text-red-300 hover:bg-red-400/15'
                    : canSend
                      ? 'border-orange-300/35 bg-orange-500 text-white shadow-[0_5px_18px_rgba(249,115,22,0.28)] hover:bg-orange-400'
                      : 'border-white/[0.06] bg-white/[0.03] text-zinc-700',
                )}
              >
                {isLoading ? <Square size={14} fill="currentColor" /> : <ArrowUp size={17} />}
              </button>
            </TooltipButton>
          </div>

          {dragging ? (
            <div className="pointer-events-none absolute inset-2 grid place-items-center rounded-2xl border border-dashed border-orange-400/55 bg-[#121212]/90 text-xs font-semibold text-orange-300">
              Suelta las imágenes para adjuntarlas
            </div>
          ) : null}
        </div>

        <DialogPrimitive.Root
          open={Boolean(selectedImage)}
          onOpenChange={(open) => {
            if (!open) setSelectedImage(null);
          }}
        >
          <DialogPrimitive.Portal>
            <DialogPrimitive.Overlay className="fixed inset-0 z-[9998] bg-black/75 backdrop-blur-md" />
            <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-[9999] max-h-[86vh] w-[min(860px,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-white/10 bg-[#111] p-2 shadow-2xl outline-none">
              <DialogPrimitive.Title className="sr-only">Vista previa de imagen</DialogPrimitive.Title>
              <DialogPrimitive.Description className="sr-only">
                Imagen que se enviará junto al mensaje.
              </DialogPrimitive.Description>
              {selectedImage ? (
                <img
                  src={selectedImage.dataUrl}
                  alt={selectedImage.name}
                  className="max-h-[80vh] w-full rounded-xl object-contain"
                />
              ) : null}
              <DialogPrimitive.Close className="absolute right-4 top-4 grid h-9 w-9 place-items-center rounded-full border border-white/15 bg-black/75 text-white hover:bg-black">
                <X size={16} />
              </DialogPrimitive.Close>
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
      </TooltipPrimitive.Provider>
    );
  },
);

PromptInputBox.displayName = 'PromptInputBox';
