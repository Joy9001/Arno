import { Dialog } from "radix-ui";
import type { ImageMessagePartComponent } from "@assistant-ui/react";
import type { ComponentProps, ReactNode } from "react";

export function ImageRoot({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
  variant?: string;
  size?: string;
}) {
  return <div className={className}>{children}</div>;
}

export function ImagePreview({
  ratio: _ratio,
  fit: _fit,
  containerClassName,
  ...props
}: ComponentProps<"img"> & {
  ratio?: string;
  fit?: string;
  containerClassName?: string;
}) {
  return (
    <span className={containerClassName}>
      <img {...props} className="max-h-56 max-w-full rounded-lg object-cover" />
    </span>
  );
}

export function ImageZoom({
  src,
  alt,
  children,
}: {
  src: string;
  alt: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button type="button" aria-label={`Enlarge ${alt}`}>
          {children}
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/80" />
        <Dialog.Content className="fixed inset-4 z-50 flex items-center justify-center">
          <Dialog.Title className="sr-only">{alt}</Dialog.Title>
          <img
            src={src}
            alt={alt}
            className="max-h-full max-w-full object-contain"
          />
          <Dialog.Close
            className="absolute right-2 top-2 rounded bg-black p-2 text-white"
            aria-label="Close image"
          >
            ×
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export const Image: ImageMessagePartComponent = ({
  image,
  filename,
  status,
}) => {
  if (status?.type === "running") return <span>Loading image…</span>;
  if (status?.type === "incomplete") return <span>Image unavailable.</span>;
  const alt = filename || "Image";
  return (
    <ImageRoot>
      <ImageZoom src={image} alt={alt}>
        <ImagePreview src={image} alt={alt} />
      </ImageZoom>
    </ImageRoot>
  );
};
