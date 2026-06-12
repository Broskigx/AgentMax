import clsx from 'clsx';
import { GradientText } from './GradientText';
import './brand.css';

interface AgentMaxLogoProps {
  size?: 'sm' | 'md' | 'lg';
  showImage?: boolean;
  showTagline?: boolean;
  tagline?: string;
  className?: string;
  animated?: boolean;
}

export function AgentMaxLogo({
  size = 'md',
  showImage = false,
  showTagline = false,
  tagline = 'Asistente de escritorio',
  className,
  animated = true,
}: AgentMaxLogoProps) {
  return (
    <div className={clsx('am-logo', `am-logo--${size}`, className)}>
      {showImage ? (
        <img
          src="/agentmax-logo.png"
          alt="AgentMax"
          className="am-logo__image"
          draggable={false}
        />
      ) : null}
      <div>
        <GradientText className="am-logo__wordmark" animated={animated}>
          AgentMax
        </GradientText>
        {showTagline ? <span className="am-logo__tagline">{tagline}</span> : null}
      </div>
    </div>
  );
}
